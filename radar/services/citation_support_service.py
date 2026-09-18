"""Layer 2: check whether cited sentences are supported by their papers."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from radar.adapters.arxiv import ArxivSearchAdapter
from radar.adapters.unpaywall import UnpaywallAdapter
from radar.config import get_settings
from radar.db import SessionLocal
from radar.llm.factory import build_analysis_llm
from radar.nli import NLIContradictionChecker
from radar.schemas import (
    CitationSupportBatch,
    CitationSupportInput,
    CitationSupportJudgeOutput,
    CitationSupportResult,
    EvidenceSpan,
    ReferenceMatch,
)
from radar.services.evidence_service import EvidenceService
from radar.services.passage_retrieval import PassageRetrievalService


PROMPT_PATH = (
    Path(__file__).parents[1] / "llm" / "prompts" / "citation_support_judge.txt"
)

_REPORTING_VERBS = (
    r"found|find|show|shows|showed|demonstrate|demonstrates|demonstrated|"
    r"report|reports|reported|observe|observes|observed|indicate|indicates|"
    r"indicated|suggest|suggests|suggested|reveal|reveals|revealed|"
    r"establish|establishes|established|conclude|concludes|concluded|"
    r"argue|argues|argued|note|notes|noted"
)
_CITATION_MARKER = re.compile(
    r"\[\s*\d+(?:\s*[,;–-]\s*\d+)*\s*\]"
)
_AUTHOR_YEAR_MARKER = re.compile(
    r"\([^()]*\b(?:18|19|20|21)\d{2}[a-z]?\b[^()]*\)"
)
_LEADING_REPORTING = re.compile(
    rf"^(?:(?:the|these)\s+authors?\s+)?(?:{_REPORTING_VERBS})"
    r"\s+(?:that\s+)?",
    re.IGNORECASE,
)
_LEADING_AUTHOR_YEAR = re.compile(
    rf"^.+?\(\s*(?:18|19|20|21)\d{{2}}[a-z]?(?:\s*[,;][^)]*)?\)"
    rf"\s*,?\s*(?:(?:the|these)\s+authors?\s+)?(?:{_REPORTING_VERBS})?"
    r"\s*(?:that\s+)?",
    re.IGNORECASE,
)
_LEADING_ACCORDING_TO = re.compile(
    r"^according\s+to\s+.+?\(\s*(?:18|19|20|21)\d{2}[a-z]?[^)]*\)"
    r"\s*,?\s*",
    re.IGNORECASE,
)
_LEADING_NUMERIC_CITATION = re.compile(
    rf"^in\s+{_CITATION_MARKER.pattern}\s*,?\s*"
    rf"(?:(?:the|these)\s+authors?\s+)?(?:{_REPORTING_VERBS})?\s*"
    r"(?:that\s+)?",
    re.IGNORECASE,
)
_LEADING_AUTHORS_OF_NUMERIC_CITATION = re.compile(
    rf"^the\s+authors?\s+of\s+{_CITATION_MARKER.pattern}\s+"
    rf"(?:{_REPORTING_VERBS})?\s*(?:that\s+)?",
    re.IGNORECASE,
)

_RECOMMENDED_ACTIONS: dict[str, list[str]] = {
    "supported": ["cite_as_supported"],
    "partially_supported": ["add_missing_qualifier", "quote_exact_condition"],
    "unsupported": ["remove_or_rephrase_claim", "replace_citation"],
    "uncertain": ["fetch_full_text", "verify_manually"],
}


def normalize_citation_claim(sentence: str) -> str:
    """Remove citation attribution while preserving the cited proposition.

    This is deliberately a small regex-based normalizer.  It is used before
    both keyword retrieval and NLI, so it must not ask a model to guess which
    parts of a sentence are evidence-bearing.
    """

    text = " ".join(str(sentence or "").split()).strip()
    if not text:
        return ""

    # Prefixes are removed before generic citation markers so that
    # ``Smith et al. (2020) found that ...`` does not leave ``Smith et al.``
    # in front of the proposition.
    for _ in range(3):
        original = text
        text = _LEADING_ACCORDING_TO.sub("", text, count=1)
        text = _LEADING_NUMERIC_CITATION.sub("", text, count=1)
        text = _LEADING_AUTHORS_OF_NUMERIC_CITATION.sub("", text, count=1)
        text = _LEADING_AUTHOR_YEAR.sub("", text, count=1)
        text = _LEADING_REPORTING.sub("", text, count=1)
        text = text.lstrip(" ,:;")
        if text == original:
            break

    # Remove standalone numeric markers and author-year parentheticals even
    # when they occur after the attribution prefix (for example, ``[12]`` at
    # the end of a citing sentence).
    text = _CITATION_MARKER.sub(" ", text)
    text = _AUTHOR_YEAR_MARKER.sub(" ", text)
    text = re.sub(r"\s+([,;:])", r"\1", text)
    text = " ".join(text.split()).strip(" ,;:")
    if text.endswith("."):
        text = text[:-1].rstrip()
    return text


class CitationSupportService:
    """Verify one or more citing sentences against retrieved paper text."""

    def __init__(
        self,
        session_factory=SessionLocal,
        llm_client=None,
        nli_checker=None,
        arxiv_adapter=None,
        unpaywall_adapter=None,
        passage_retrieval: PassageRetrievalService | None = None,
    ):
        self.session_factory = session_factory
        settings = get_settings()
        self.llm_client = (
            llm_client if llm_client is not None else build_analysis_llm()
        )
        self.nli_checker = (
            nli_checker
            if nli_checker is not None
            else (NLIContradictionChecker() if settings.nli_enabled else None)
        )
        self.passage_retrieval = (
            passage_retrieval
            if passage_retrieval is not None
            else PassageRetrievalService()
        )
        self.arxiv_adapter = (
            arxiv_adapter
            if arxiv_adapter is not None
            else ArxivSearchAdapter(settings.data_dir / "cache" / "arxiv")
        )
        self.unpaywall_adapter = (
            unpaywall_adapter
            if unpaywall_adapter is not None
            else UnpaywallAdapter(email=settings.crossref_mailto)
        )

    def resolve_full_text(
        self, reference: ReferenceMatch
    ) -> tuple[str | None, Literal["full_text", "abstract_only", "unavailable"]]:
        """Resolve public full text for a reference without consulting the DB."""

        if reference.arxiv_id:
            try:
                text = self.arxiv_adapter.fetch_full_text(reference.arxiv_id)
                if text:
                    return text, "full_text"
            except Exception:
                pass
        elif reference.doi:
            try:
                pdf_url = self.unpaywall_adapter.get_oa_pdf_url(reference.doi)
                if pdf_url:
                    text = self.unpaywall_adapter.download_pdf_text(pdf_url)
                    if text:
                        return text, "full_text"
            except Exception:
                pass

        # ReferenceMatch currently has no abstract field, but accepting one
        # from an injected/extended object keeps this resolver future-proof.
        abstract = getattr(reference, "abstract", None)
        if isinstance(abstract, str) and abstract.strip():
            return abstract, "abstract_only"
        return None, "unavailable"

    @staticmethod
    def _result(
        input: CitationSupportInput,
        normalized_claim: str,
        *,
        category: Literal[
            "supported", "partially_supported", "unsupported", "uncertain"
        ] = "uncertain",
        confidence: float = 0.0,
        evidence: EvidenceSpan | None = None,
        full_text_status: Literal[
            "full_text", "abstract_only", "unavailable"
        ] = "unavailable",
        reasons: list[str] | None = None,
    ) -> CitationSupportResult:
        return CitationSupportResult(
            reference=input.reference,
            citation_sentence=input.citation_sentence,
            normalized_claim=normalized_claim,
            category=category,
            confidence=confidence,
            evidence_quote=evidence.quote if evidence else "",
            locator=evidence.locator if evidence else "",
            full_text_status=full_text_status,
            reasons=list(reasons or []),
            recommended_actions=list(_RECOMMENDED_ACTIONS[category]),
        )

    @staticmethod
    def _nli_label_and_score(value) -> tuple[str, float] | None:
        if value is None:
            return None
        if not isinstance(value, dict):
            label = getattr(value, "label", "")
            scores = getattr(value, "scores", {})
            value = {"label": label, "scores": scores}
        label = str(value.get("label") or "").lower().strip()
        scores = value.get("scores") or {}
        raw_score = (
            scores.get(label, value.get("score", 0.0))
            if isinstance(scores, dict)
            else value.get("score", 0.0)
        )
        try:
            score = float(raw_score)
        except (TypeError, ValueError):
            score = 0.0
        return label, score

    @staticmethod
    def _numeric_tokens(text: str) -> list[str]:
        return re.findall(r"(?<![\w.])-?\d+(?:,\d{3})*(?:\.\d+)?%?", text)

    def _heuristic_judgment(
        self, normalized_claim: str, evidence: EvidenceSpan
    ) -> tuple[
        Literal["supported", "partially_supported", "unsupported", "uncertain"],
        float,
    ]:
        nli_result = None
        if self.nli_checker is not None:
            try:
                nli_result = self.nli_checker.check(normalized_claim, evidence.quote)
            except Exception:
                nli_result = None
        parsed_nli = self._nli_label_and_score(nli_result)
        if parsed_nli is not None:
            label, score = parsed_nli
            if label == "entailment" and score >= 0.55:
                return "supported", max(score, 0.5)
            if label == "contradiction":
                return "unsupported", max(score, 0.5)
            if label == "neutral":
                return "uncertain", max(score, 0.5)

        numeric_tokens = self._numeric_tokens(normalized_claim)
        if numeric_tokens and all(token in evidence.quote for token in numeric_tokens):
            return "partially_supported", 0.4
        return "uncertain", 0.4

    def _llm_judgment(
        self,
        normalized_claim: str,
        evidence_candidates: list[EvidenceSpan],
    ) -> CitationSupportJudgeOutput | None:
        if self.llm_client is None:
            return None
        try:
            instructions = PROMPT_PATH.read_text(encoding="utf-8").strip()
            payload = {
                "normalized_claim": normalized_claim,
                "candidate_passages": [
                    candidate.model_dump() for candidate in evidence_candidates
                ],
            }
            output = self.llm_client.generate_structured(
                stage="citation_support_judge",
                prompt=(
                    f"{instructions}\n\nINPUT JSON:\n"
                    f"{json.dumps(payload, ensure_ascii=False, indent=2)}"
                ),
                response_model=CitationSupportJudgeOutput,
            )
            if isinstance(output, CitationSupportJudgeOutput):
                return output
            return CitationSupportJudgeOutput.model_validate(output)
        except Exception:
            return None

    def check(self, input: CitationSupportInput) -> CitationSupportResult:
        """Check one citation and degrade to an uncertain result on failures."""

        try:
            if not isinstance(input, CitationSupportInput):
                input = CitationSupportInput.model_validate(input)
            normalized_claim = normalize_citation_claim(input.citation_sentence)
            if not normalized_claim:
                return self._result(
                    input,
                    normalized_claim,
                    confidence=0.0,
                    reasons=["empty_claim"],
                )

            text: str | None
            if input.full_text is not None:
                text = input.full_text
                full_text_status = "full_text"
            elif input.abstract and input.abstract.strip():
                text = input.abstract
                full_text_status = "abstract_only"
            else:
                text, full_text_status = self.resolve_full_text(input.reference)

            if full_text_status == "unavailable" or text is None:
                return CitationSupportResult(
                    **self._result(
                        input,
                        normalized_claim,
                        confidence=0.3,
                        full_text_status="unavailable",
                        reasons=["full_text_unavailable"],
                    ).model_dump(exclude={"recommended_actions"}),
                    recommended_actions=["retry_with_full_text", "use_abstract_only"],
                )

            limit = 3 if full_text_status == "abstract_only" else 5
            try:
                evidence_candidates = self.passage_retrieval.retrieve(
                    normalized_claim, text, top_k=limit
                )
            except Exception:
                evidence_candidates = []
            if not evidence_candidates:
                evidence_candidates = EvidenceService.rank_relevant_evidence(
                    query_terms=[normalized_claim],
                    content=text,
                    source_snapshot_id="",
                    limit=limit,
                )
            reasons: list[str] = []
            if full_text_status == "abstract_only":
                reasons.append("abstract_only_evidence_quality_limited")
            if not evidence_candidates:
                reasons.append("no_relevant_passage")
                return self._result(
                    input,
                    normalized_claim,
                    confidence=0.2,
                    full_text_status=full_text_status,
                    reasons=reasons,
                )

            evidence = evidence_candidates[0]
            judge = self._llm_judgment(normalized_claim, evidence_candidates)
            if judge is not None:
                category = judge.category
                confidence = judge.confidence
                reasons.extend(judge.reasons)
            else:
                category, confidence = self._heuristic_judgment(
                    normalized_claim, evidence
                )
            return self._result(
                input,
                normalized_claim,
                category=category,
                confidence=confidence,
                evidence=evidence,
                full_text_status=full_text_status,
                reasons=reasons,
            )
        except Exception:
            # A malformed passage, adapter, or injected dependency must not
            # prevent the rest of a citation batch from being checked.
            return self._result(
                input,
                normalize_citation_claim(input.citation_sentence),
                confidence=0.0,
                reasons=["citation_check_failed"],
            )

    def check_batch(
        self, inputs: list[CitationSupportInput]
    ) -> CitationSupportBatch:
        results: list[CitationSupportResult] = []
        for raw_input in inputs:
            try:
                item = (
                    raw_input
                    if isinstance(raw_input, CitationSupportInput)
                    else CitationSupportInput.model_validate(raw_input)
                )
                results.append(self.check(item))
            except Exception:
                # Inputs normally arrive validated by FastAPI, but preserve
                # batch semantics for callers using the service directly.
                item = (
                    raw_input
                    if isinstance(raw_input, CitationSupportInput)
                    else CitationSupportInput.model_validate(raw_input)
                )
                results.append(
                    self._result(
                        item,
                        normalize_citation_claim(item.citation_sentence),
                        confidence=0.0,
                        reasons=["citation_check_failed"],
                    )
                )

        summary = {
            "total": len(results),
            "supported": sum(item.category == "supported" for item in results),
            "partially_supported": sum(
                item.category == "partially_supported" for item in results
            ),
            "unsupported": sum(item.category == "unsupported" for item in results),
            "uncertain": sum(item.category == "uncertain" for item in results),
        }
        return CitationSupportBatch(
            results=results,
            checked_at=datetime.now(timezone.utc).isoformat(),
            summary=summary,
        )
