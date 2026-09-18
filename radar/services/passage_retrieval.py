"""Passage-level hybrid retrieval for citation evidence."""

from __future__ import annotations

import math
import re
from collections import Counter

import numpy as np

from radar.schemas import EvidenceSpan
from radar.services.evidence_service import SENTENCE_BREAK, _keywords


def _tokens(value: str) -> list[str]:
    """Tokenize text using the same lightweight scheme as source retrieval."""

    return [
        token
        for token in re.findall(r"[a-z0-9]+", value.lower())
        if len(token) > 2
    ]


def _bm25_score(
    query_tokens: list[str],
    document_tokens: list[str],
    doc_lengths: list[int],
    avg_doc_length: float,
    total_docs: int,
    token_df: dict[str, int],
    k1: float = 1.2,
    b: float = 0.75,
) -> float:
    """BM25 scoring for one passage against a query."""

    if not query_tokens or not document_tokens:
        return 0.0

    doc_length = len(document_tokens)
    doc_counts = Counter(document_tokens)
    score = 0.0
    for token in set(query_tokens):
        term_frequency = doc_counts.get(token, 0)
        if term_frequency == 0:
            continue
        document_frequency = token_df.get(token, 1)
        inverse_document_frequency = math.log(
            1.0
            + (total_docs - document_frequency + 0.5)
            / (document_frequency + 0.5)
        )
        numerator = term_frequency * (k1 + 1)
        denominator = term_frequency + k1 * (
            1 - b + b * doc_length / max(avg_doc_length, 1)
        )
        score += inverse_document_frequency * numerator / denominator

    return score


class PassageRetrievalService:
    """Retrieve the passages most likely to support a claim."""

    def __init__(
        self,
        embedding_client=None,
        cross_encoder=None,
        bm25_weight=0.35,
        lexical_weight=0.25,
        vector_weight=0.40,
        rerank_keep=6,
    ):
        self.embedding_client = embedding_client
        self.cross_encoder = cross_encoder
        self.bm25_weight = bm25_weight
        self.lexical_weight = lexical_weight
        self.vector_weight = vector_weight
        self.rerank_keep = rerank_keep

    @staticmethod
    def _split_passages(content: str) -> list[tuple[str, str]]:
        """Use the deterministic paragraph/sentence units used by EvidenceService."""

        passages: list[tuple[str, str]] = []
        sentence_number = 0
        for paragraph_number, raw_paragraph in enumerate(
            content.split("\n"), start=1
        ):
            paragraph = raw_paragraph.strip()
            if not paragraph:
                continue
            sentences = [
                part.strip()
                for part in SENTENCE_BREAK.split(paragraph)
                if part.strip()
            ]
            if len(sentences) == 1:
                passages.append((f"paragraph:{paragraph_number}", paragraph))
                continue
            for sentence in sentences:
                sentence_number += 1
                passages.append((f"sentence:{sentence_number}", sentence))
        return passages

    @staticmethod
    def _normalize_nonnegative(values: list[float]) -> np.ndarray:
        scores = np.asarray(values, dtype=float)
        if scores.size == 0:
            return scores
        scores = np.where(np.isfinite(scores) & (scores > 0), scores, 0.0)
        maximum = float(scores.max())
        if maximum <= 0:
            return np.zeros_like(scores)
        return scores / maximum

    @staticmethod
    def _normalize_similarity(values: list[float]) -> np.ndarray:
        scores = np.asarray(values, dtype=float)
        if scores.size == 0:
            return scores
        scores = np.where(np.isfinite(scores), scores, 0.0)
        minimum = float(scores.min())
        maximum = float(scores.max())
        if maximum - minimum < 1e-8:
            return np.zeros_like(scores)
        return (scores - minimum) / (maximum - minimum)

    def _vector_scores(self, query: str, passages: list[str]) -> list[float]:
        """Return cosine similarities, degrading to zeros on any embedding error."""

        if self.embedding_client is None or not passages:
            return [0.0] * len(passages)
        try:
            vectors = np.asarray(
                self.embedding_client.embed([query, *passages]), dtype=float
            )
            if vectors.ndim != 2 or vectors.shape[0] != len(passages) + 1:
                raise ValueError("embedding_batch_shape_mismatch")
            query_vector = vectors[0]
            passage_vectors = vectors[1:]
            query_norm = float(np.linalg.norm(query_vector))
            passage_norms = np.linalg.norm(passage_vectors, axis=1)
            denominators = passage_norms * query_norm
            similarities = np.zeros(len(passages), dtype=float)
            valid = denominators > 0
            if np.any(valid):
                similarities[valid] = (
                    passage_vectors[valid] @ query_vector
                ) / denominators[valid]
            similarities = np.where(np.isfinite(similarities), similarities, 0.0)
            return similarities.tolist()
        except Exception:
            return [0.0] * len(passages)

    def _entailment_score(self, query: str, passage: str) -> float:
        if self.cross_encoder is None:
            return 0.0
        try:
            checker = getattr(self.cross_encoder, "check", self.cross_encoder)
            result = checker(query, passage)
            if not isinstance(result, dict):
                scores = getattr(result, "scores", {})
            else:
                scores = result.get("scores") or {}
            value = scores.get("entailment", 0.0) if isinstance(scores, dict) else 0.0
            value = float(value)
            if not math.isfinite(value):
                return 0.0
            return min(max(value, 0.0), 1.0)
        except Exception:
            return 0.0

    def retrieve(
        self, query: str, content: str, top_k: int = 3
    ) -> list[EvidenceSpan]:
        """Return up to ``top_k`` ranked evidence passages.

        Retrieval is deliberately best-effort: a provider failure only removes
        that provider's contribution and never prevents lexical results from
        being returned.
        """

        try:
            if not isinstance(query, str) or not query.strip():
                return []
            if not isinstance(content, str) or not content.strip() or top_k <= 0:
                return []
            passages_with_locators = self._split_passages(content)
            if not passages_with_locators:
                return []

            query_keywords = _keywords([query])
            lexical_scores = [
                len(query_keywords & _keywords([passage]))
                / max(len(query_keywords), 1)
                for _, passage in passages_with_locators
            ]

            query_tokens = _tokens(query)
            document_tokens = [_tokens(passage) for _, passage in passages_with_locators]
            document_lengths = [len(tokens) for tokens in document_tokens]
            average_document_length = (
                sum(document_lengths) / len(document_lengths)
                if document_lengths
                else 1.0
            )
            token_df: dict[str, int] = {}
            for tokens in document_tokens:
                for token in set(tokens):
                    token_df[token] = token_df.get(token, 0) + 1
            bm25_scores = [
                _bm25_score(
                    query_tokens,
                    tokens,
                    document_lengths,
                    average_document_length,
                    len(document_tokens),
                    token_df,
                )
                for tokens in document_tokens
            ]
            vector_scores = self._vector_scores(
                query, [passage for _, passage in passages_with_locators]
            )

            lexical_normalized = self._normalize_nonnegative(lexical_scores)
            bm25_normalized = self._normalize_nonnegative(bm25_scores)
            vector_normalized = self._normalize_similarity(vector_scores)
            fused_scores = (
                self.lexical_weight * lexical_normalized
                + self.bm25_weight * bm25_normalized
                + self.vector_weight * vector_normalized
            )

            # If the hybrid legs have no signal, retain the deterministic
            # lexical order so an optional reranker still gets candidates.
            if not np.any(fused_scores > 0):
                fused_scores = lexical_normalized.copy()

            ranked_indices = sorted(
                range(len(passages_with_locators)),
                key=lambda index: (-float(fused_scores[index]), index),
            )
            head_size = max(10, top_k * 2)
            head_indices = ranked_indices[:head_size]

            if self.cross_encoder is not None:
                reranked = []
                for index in head_indices:
                    entailment_probability = self._entailment_score(
                        query, passages_with_locators[index][1]
                    )
                    blended_score = 0.5 * float(fused_scores[index]) + 0.5 * entailment_probability
                    reranked.append((blended_score, float(fused_scores[index]), index))
                reranked.sort(key=lambda item: (-item[0], -item[1], item[2]))
                head_indices = [item[2] for item in reranked]

            return [
                EvidenceSpan(
                    quote=passages_with_locators[index][1],
                    locator=passages_with_locators[index][0],
                    source_snapshot_id="",
                )
                for index in head_indices[:top_k]
            ]
        except Exception:
            return []
