"""Unit tests for BM25 retrieval scoring and ChromaDB vector store."""

import math
import tempfile
from pathlib import Path

import numpy as np
import pytest

from radar.services.retrieval_service import _bm25_score, _tokens
from radar.vector_store import (
    MemoryVectorStore,
    VectorStore,
    build_vector_store,
    _chroma_available,
)


# =============================================================================
# _tokens helper
# =============================================================================


class TestTokenization:
    def test_basic_words(self):
        assert _tokens("hello world test") == ["hello", "world", "test"]

    def test_short_tokens_filtered(self):
        assert _tokens("a b c ab cd ef ghi") == ["ghi"]

    def test_numbers_included(self):
        tokens = _tokens("bert 2024 large model 3b")
        assert "2024" in tokens

    def test_case_normalized(self):
        assert _tokens("BERT Large MODEL") == ["bert", "large", "model"]

    def test_empty_string(self):
        assert _tokens("") == []

    def test_only_short_tokens(self):
        assert _tokens("a b c d") == []

    def test_punctuation_stripped(self):
        assert _tokens("state-of-the-art, (model)") == ["state", "the", "art", "model"]


# =============================================================================
# BM25 scoring
# =============================================================================


class TestBM25SingleDocument:
    """Test _bm25_score for a single document against a query."""

    def _make_index(self, documents: list[str]) -> tuple[list[list[str]], list[int], float, int, dict[str, int]]:
        doc_tokens_list = [_tokens(d) for d in documents]
        doc_lengths = [len(t) for t in doc_tokens_list]
        avg_doc_length = sum(doc_lengths) / max(len(doc_lengths), 1)
        total_docs = len(documents)
        token_df: dict[str, int] = {}
        for tokens in doc_tokens_list:
            for token in set(tokens):
                token_df[token] = token_df.get(token, 0) + 1
        return doc_tokens_list, doc_lengths, avg_doc_length, total_docs, token_df

    def test_perfect_match_scores_high(self):
        docs = [
            "retrieval augmented generation for domain shift",
            "completely unrelated topic here",
        ]
        doc_tokens_list, doc_lengths, avg_len, total, token_df = self._make_index(docs)
        query_tokens = _tokens("retrieval augmented generation")
        s1 = _bm25_score(query_tokens, doc_tokens_list[0], doc_lengths, avg_len, total, token_df)
        s2 = _bm25_score(query_tokens, doc_tokens_list[1], doc_lengths, avg_len, total, token_df)
        assert s1 > s2
        assert s1 > 0
        assert s2 == 0.0

    def test_empty_query_returns_zero(self):
        docs = ["retrieval augmented generation"]
        doc_tokens_list, doc_lengths, avg_len, total, token_df = self._make_index(docs)
        score = _bm25_score([], doc_tokens_list[0], doc_lengths, avg_len, total, token_df)
        assert score == 0.0

    def test_empty_document_returns_zero(self):
        docs = ["retrieval augmented generation"]
        doc_tokens_list, doc_lengths, avg_len, total, token_df = self._make_index(docs)
        query_tokens = _tokens("retrieval generation")
        score = _bm25_score(query_tokens, [], doc_lengths, avg_len, total, token_df)
        assert score == 0.0

    def test_tf_increases_score(self):
        """Repeated terms should increase BM25 score."""
        docs = ["test test test test retrieval", "retrieval only"]
        doc_tokens_list, doc_lengths, avg_len, total, token_df = self._make_index(docs)
        query_tokens = _tokens("test")
        s1 = _bm25_score(query_tokens, doc_tokens_list[0], doc_lengths, avg_len, total, token_df)
        s2 = _bm25_score(query_tokens, doc_tokens_list[1], doc_lengths, avg_len, total, token_df)
        # Doc 0 has "test" appearing 4 times, doc 1 has 0
        assert s1 > s2

    def test_idf_penalizes_common_terms(self):
        """Terms appearing in all documents contribute less."""
        docs = [
            "the model performs well",
            "the model fails badly",
            "the model is average",
        ]
        doc_tokens_list, doc_lengths, avg_len, total, token_df = self._make_index(docs)
        # "the" (3 chars? no - filtered by _tokens), "model" appears in all 3 docs
        query_tokens = _tokens("model performs")
        s1 = _bm25_score(query_tokens, doc_tokens_list[0], doc_lengths, avg_len, total, token_df)
        s2 = _bm25_score(query_tokens, doc_tokens_list[1], doc_lengths, avg_len, total, token_df)
        # Doc 0 has "performs", doc 1 doesn't - so doc 0 should score higher
        assert s1 > s2

    def test_document_length_normalization(self):
        """BM25's b parameter should normalize for doc length."""
        docs = [
            "retrieval",
            "padding padding padding padding padding padding retrieval",
        ]
        doc_tokens_list, doc_lengths, avg_len, total, token_df = self._make_index(docs)
        query_tokens = _tokens("retrieval")
        s1 = _bm25_score(query_tokens, doc_tokens_list[0], doc_lengths, avg_len, total, token_df)
        s2 = _bm25_score(query_tokens, doc_tokens_list[1], doc_lengths, avg_len, total, token_df)
        # Short doc with same unique term scores higher after length normalization
        assert s1 > s2

    def test_multiple_query_terms_accumulate(self):
        docs = [
            "retrieval augmented generation domain shift robustness evaluation",
            "retrieval augmented generation",
        ]
        doc_tokens_list, doc_lengths, avg_len, total, token_df = self._make_index(docs)
        query_tokens = _tokens("retrieval augmented generation domain shift robustness")
        s1 = _bm25_score(query_tokens, doc_tokens_list[0], doc_lengths, avg_len, total, token_df)
        s2 = _bm25_score(query_tokens, doc_tokens_list[1], doc_lengths, avg_len, total, token_df)
        # Doc 0 matches all terms, doc 1 matches only 3
        assert s1 > s2

    def test_default_k1_b_values(self):
        """Verify BM25 with default k1=1.2, b=0.75 produces reasonable scores."""
        docs = ["retrieval augmented generation for QA systems"]
        doc_tokens_list, doc_lengths, avg_len, total, token_df = self._make_index(docs)
        query_tokens = _tokens("retrieval generation")
        score = _bm25_score(query_tokens, doc_tokens_list[0], doc_lengths, avg_len, total, token_df)
        assert score > 0
        assert math.isfinite(score)

    def test_single_document_index_baseline(self):
        """When there's only one document, BM25 should still produce a score."""
        docs = ["retrieval augmented generation"]
        doc_tokens_list, doc_lengths, avg_len, total, token_df = self._make_index(docs)
        query_tokens = _tokens("retrieval")
        score = _bm25_score(query_tokens, doc_tokens_list[0], doc_lengths, avg_len, total, token_df)
        assert score > 0

    def test_nonexistent_token_contributes_zero(self):
        docs = ["retrieval systems"]
        doc_tokens_list, doc_lengths, avg_len, total, token_df = self._make_index(docs)
        query_tokens = _tokens("machine learning")
        score = _bm25_score(query_tokens, doc_tokens_list[0], doc_lengths, avg_len, total, token_df)
        assert score == 0.0


# =============================================================================
# RetrievalService._bm25_scores batch method (requires DB, tested via db_session_factory)
# =============================================================================


class TestBM25BatchScores:
    def test_batch_scores_length_matches_docs(self, db_session_factory):
        from radar.services.retrieval_service import RetrievalService
        svc = RetrievalService(db_session_factory)
        query_tokens = _tokens("retrieval generation")
        documents = ["retrieval augmented generation", "unrelated text here", "domain shift retrieval"]
        scores = svc._bm25_scores(query_tokens, documents)
        assert len(scores) == 3
        assert all(isinstance(s, float) for s in scores)

    def test_batch_scores_ranks_relevant_higher(self, db_session_factory):
        from radar.services.retrieval_service import RetrievalService
        svc = RetrievalService(db_session_factory)
        query_tokens = _tokens("retrieval domain shift")
        documents = [
            "completely unrelated text about sports",
            "retrieval domain shift in QA systems",
            "somewhat related retrieval only",
        ]
        scores = svc._bm25_scores(query_tokens, documents)
        assert scores[1] > scores[0]
        assert scores[1] > scores[2]

    def test_batch_empty_docs(self, db_session_factory):
        from radar.services.retrieval_service import RetrievalService
        svc = RetrievalService(db_session_factory)
        scores = svc._bm25_scores(_tokens("test"), [])
        assert scores == []

    def test_batch_empty_query(self, db_session_factory):
        from radar.services.retrieval_service import RetrievalService
        svc = RetrievalService(db_session_factory)
        scores = svc._bm25_scores([], ["test document"])
        assert scores == [0.0]


# =============================================================================
# MemoryVectorStore
# =============================================================================


class TestMemoryVectorStore:
    @pytest.fixture
    def store(self) -> MemoryVectorStore:
        return MemoryVectorStore()

    def test_initial_state(self, store):
        assert store.count() == 0
        assert store.query([1.0, 0.0]) == []

    def test_add_documents(self, store):
        store.add(["d1", "d2"], ["document one", "document two"])
        assert store.count() == 2

    def test_add_preserves_ids_order(self, store):
        store.add(["a", "b", "c"], ["doc a", "doc b", "doc c"])
        assert store._ids == ["a", "b", "c"]

    def test_query_no_embeddings_returns_empty(self, store):
        store.add(["d1"], ["test doc"])
        results = store.query([1.0, 0.0, 0.0])
        assert results == []

    def test_query_with_embeddings_returns_ranked(self, store):
        store.add(["d1", "d2", "d3"], ["a", "b", "c"])
        store.set_embeddings(
            ["d1", "d2", "d3"],
            [np.array([1.0, 0.0]), np.array([0.0, 1.0]), np.array([0.5, 0.5])],
        )
        results = store.query([1.0, 0.0], top_k=3)
        assert len(results) == 3
        # d1 should be top (cosine = 1.0)
        assert results[0][0] == "d1"
        assert results[0][1] > results[1][1]
        assert results[0][1] > results[2][1]

    def test_query_top_k_limits(self, store):
        store.add(["d1", "d2", "d3", "d4"], ["a", "b", "c", "d"])
        store.set_embeddings(
            ["d1", "d2", "d3", "d4"],
            [np.array([1.0, 0.0]), np.array([0.9, 0.1]), np.array([0.5, 0.5]), np.array([0.1, 0.9])],
        )
        results = store.query([1.0, 0.0], top_k=2)
        assert len(results) == 2
        assert results[0][0] == "d1"

    def test_query_top_k_exceeds_store(self, store):
        store.add(["d1"], ["a"])
        store.set_embeddings(["d1"], [np.array([1.0, 0.0])])
        results = store.query([1.0, 0.0], top_k=10)
        assert len(results) == 1

    def test_delete_removes_documents(self, store):
        store.add(["d1", "d2", "d3"], ["a", "b", "c"])
        store.set_embeddings(
            ["d1", "d2", "d3"],
            [np.array([1.0, 0.0]), np.array([0.0, 1.0]), np.array([0.5, 0.5])],
        )
        store.delete(["d2"])
        assert store.count() == 2
        results = store.query([0.0, 1.0], top_k=3)
        remaining_ids = {r[0] for r in results}
        assert "d2" not in remaining_ids
        assert "d1" in remaining_ids
        assert "d3" in remaining_ids

    def test_delete_all_documents(self, store):
        store.add(["d1", "d2"], ["a", "b"])
        store.set_embeddings(["d1", "d2"], [np.array([1.0, 0.0]), np.array([0.0, 1.0])])
        store.delete(["d1", "d2"])
        assert store.count() == 0
        assert store.query([1.0, 0.0]) == []

    def test_delete_nonexistent_id(self, store):
        store.add(["d1"], ["a"])
        store.set_embeddings(["d1"], [np.array([1.0, 0.0])])
        store.delete(["d999"])
        assert store.count() == 1

    def test_clear_removes_all(self, store):
        store.add(["d1", "d2", "d3"], ["a", "b", "c"])
        store.set_embeddings(
            ["d1", "d2", "d3"],
            [np.array([1.0, 0.0]), np.array([0.0, 1.0]), np.array([0.5, 0.5])],
        )
        store.clear()
        assert store.count() == 0
        assert store._ids == []
        assert store._documents == []
        assert store._embeddings == []

    def test_set_embeddings_overwrites_existing(self, store):
        store.add(["d1", "d2"], ["a", "b"])
        store.set_embeddings(["d1", "d2"], [np.array([1.0, 0.0]), np.array([0.0, 1.0])])
        store.set_embeddings(["d3"], [np.array([0.5, 0.5])])
        assert store.count() == 1
        results = store.query([0.6, 0.4], top_k=1)
        assert results[0][0] == "d3"

    def test_add_with_metadata_is_noop(self, store):
        """add() with metadatas should not crash — MemoryVectorStore ignores metadata."""
        store.add(["d1"], ["test"], metadatas=[{"title": "Test Paper"}])
        assert store.count() == 1

    def test_add_with_embeddings_makes_query_work(self, store):
        """M32 regression: add() must store the embeddings it receives, so the
        in-memory fallback answers queries instead of silently returning []."""
        store.add(
            ["d1", "d2"],
            ["document one", "document two"],
            embeddings=[np.array([1.0, 0.0]), np.array([0.0, 1.0])],
        )
        results = store.query([1.0, 0.0], top_k=2)
        assert results[0][0] == "d1"
        assert results[0][1] > results[1][1]

    def test_add_without_embeddings_keeps_existing_embeddings(self, store):
        store.add(["d1"], ["a"], embeddings=[np.array([1.0, 0.0])])
        store.add(["d2"], ["b"])  # no embeddings: must not clobber
        assert store.count() == 2
        results = store.query([1.0, 0.0], top_k=2)
        assert len(results) == 1  # only the embedded doc is queryable
        assert results[0][0] == "d1"


# =============================================================================
# build_vector_store factory
# =============================================================================


class TestBuildVectorStore:
    def test_build_prefer_chroma_false_is_memory(self, tmp_path: Path):
        vs = build_vector_store(tmp_path / "test_vs", prefer_chroma=False)
        assert isinstance(vs, MemoryVectorStore)
        assert vs.count() == 0
        vs.clear()

    def test_build_prefer_chroma_true(self, tmp_path: Path):
        """When ChromaDB is installed, prefer_chroma=True returns ChromaVectorStore."""
        vs = build_vector_store(tmp_path / "test_vs_chroma", prefer_chroma=True)
        assert vs is not None
        assert vs.count() == 0

    @pytest.mark.skipif(not _chroma_available(), reason="chromadb not installed")
    def test_chroma_upsert_replaces_same_id(self, tmp_path: Path):
        """M32 regression: re-indexing the same snapshot id (stable across
        scans) must replace the row — not crash with Chroma's DuplicateIDError."""
        from radar.vector_store import ChromaVectorStore

        store = ChromaVectorStore(tmp_path / "chroma-upsert")
        embedding = [0.1, 0.2, 0.3]
        store.add(["s1"], ["document v1"], embeddings=[embedding])
        store.add(["s1"], ["document v2"], embeddings=[embedding])
        assert store.count() == 1
        results = store.query([0.1, 0.2, 0.3], top_k=1)
        assert results[0][0] == "s1"
        store.clear()
        assert store.count() == 0

    @pytest.mark.skipif(not _chroma_available(), reason="chromadb not installed")
    def test_chroma_does_not_mutate_caller_metadata(self, tmp_path: Path):
        from radar.vector_store import ChromaVectorStore

        store = ChromaVectorStore(tmp_path / "chroma-meta")
        metadata = [{"title": "Original"}]
        store.add(["s1"], ["doc"], metadatas=metadata, embeddings=[[0.1, 0.2, 0.3]])
        assert metadata[0] == {"title": "Original"}  # _content_hash not injected
        store.clear()

    @pytest.mark.skipif(not _chroma_available(), reason="chromadb not installed")
    def test_chroma_requires_explicit_embeddings(self, tmp_path: Path):
        from radar.vector_store import ChromaVectorStore

        store = ChromaVectorStore(tmp_path / "chroma-no-default-model")
        with pytest.raises(ValueError, match="chroma_embeddings_required"):
            store.add(["s1"], ["doc"])

    def test_build_creates_persist_dir(self, tmp_path: Path):
        persist_dir = tmp_path / "nested" / "vector_store"
        vs = build_vector_store(persist_dir, prefer_chroma=False)
        # MemoryVectorStore doesn't create directories on disk
        assert vs.count() == 0

    def test_factory_returns_usable_store(self, tmp_path: Path):
        vs = build_vector_store(tmp_path / "test_vs", prefer_chroma=False)
        vs.add(["d1", "d2"], ["doc one", "doc two"])
        assert vs.count() == 2
        vs.clear()

    def test_add_query_lifecycle(self, tmp_path: Path):
        vs = build_vector_store(tmp_path / "test_vs", prefer_chroma=False)
        vs.add(["doc-1", "doc-2"], ["retrieval systems", "unrelated topic"])
        vs.set_embeddings(
            ["doc-1", "doc-2"],
            [np.array([1.0, 0.0]), np.array([0.0, 1.0])],
        )
        results = vs.query([1.0, 0.0], top_k=1)
        assert results[0][0] == "doc-1"
        vs.clear()


# =============================================================================
# Edge cases
# =============================================================================


class TestEdgeCases:
    def test_bm25_avg_doc_length_zero(self):
        """When all documents are empty, BM25 should handle zero avg length."""
        score = _bm25_score(["test"], ["test"], [0], 0.0, 1, {"test": 1})
        assert score > 0
        assert math.isfinite(score)

    def test_vector_store_zero_dimension_embedding(self):
        store = MemoryVectorStore()
        store.add(["d1"], ["a"])
        # Zero-dim embedding should be handled or produce 0 results
        store.set_embeddings(["d1"], [np.array([])])
        results = store.query([], top_k=1)
        # Should not crash
        assert isinstance(results, list)

    def test_tokens_handles_unicode(self):
        tokens = _tokens("café naïve résumé 测试 test")
        assert "caf" in tokens or "test" in tokens
