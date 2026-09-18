"""Offline tests for Layer 2 citation-support checks."""

from radar.schemas import CitationSupportInput, ReferenceMatch
from radar.services import citation_support_service as support_module
from radar.services.citation_support_service import (
    CitationSupportService,
    normalize_citation_claim,
)


def _reference(**updates) -> ReferenceMatch:
    values = {
        "source_kind": "arxiv",
        "external_id": "arxiv:1234.5678",
        "title": "A Test Paper",
        "arxiv_id": "1234.5678",
    }
    values.update(updates)
    return ReferenceMatch(**values)


class _FakeNLI:
    def __init__(self, label="entailment", score=0.92):
        self.label = label
        self.score = score

    def check(self, claim, evidence):
        return {"label": self.label, "scores": {self.label: self.score}}


class _NoopAdapter:
    def fetch_full_text(self, arxiv_id):
        raise AssertionError("network adapter should not be called")


def test_normalize_citation_claim_strips_attribution_and_keeps_numbers():
    sentence = (
        "Smith et al. (2020) found that the method improved F1 by 7.2% "
        "on 3 datasets [12]."
    )

    normalized = normalize_citation_claim(sentence)

    assert normalized == "the method improved F1 by 7.2% on 3 datasets"


def test_check_full_text_with_entailment_is_supported(monkeypatch):
    monkeypatch.setattr(support_module, "build_analysis_llm", lambda: None)
    service = CitationSupportService(
        llm_client=None,
        nli_checker=_FakeNLI(),
        arxiv_adapter=_NoopAdapter(),
    )
    citation = CitationSupportInput(
        reference=_reference(),
        citation_sentence="Smith et al. (2020) found that the method improves F1 by 7.2%.",
        full_text="The method improves F1 by 7.2% on the test set.",
    )

    result = service.check(citation)

    assert result.category == "supported"
    assert result.full_text_status == "full_text"
    assert result.evidence_quote == "The method improves F1 by 7.2% on the test set."


def test_check_abstract_only_marks_limited_evidence(monkeypatch):
    monkeypatch.setattr(support_module, "build_analysis_llm", lambda: None)
    service = CitationSupportService(
        llm_client=None,
        nli_checker=_FakeNLI(label="neutral", score=0.4),
        arxiv_adapter=_NoopAdapter(),
    )
    citation = CitationSupportInput(
        reference=_reference(),
        citation_sentence="The method improves F1 by 7.2%.",
        abstract="The method improves F1 by 7.2% on the test set.",
    )

    result = service.check(citation)

    assert result.full_text_status == "abstract_only"
    assert "abstract_only_evidence_quality_limited" in result.reasons


def test_check_without_text_is_uncertain(monkeypatch):
    monkeypatch.setattr(support_module, "build_analysis_llm", lambda: None)
    service = CitationSupportService(
        llm_client=None,
        nli_checker=_FakeNLI(),
        arxiv_adapter=_NoopAdapter(),
    )
    citation = CitationSupportInput(
        reference=_reference(arxiv_id=None, external_id="paper:1"),
        citation_sentence="The method improves F1 by 7.2%.",
    )

    result = service.check(citation)

    assert result.category == "uncertain"
    assert result.confidence == 0.3
    assert result.full_text_status == "unavailable"
    assert result.reasons == ["full_text_unavailable"]


def test_check_batch_summary(monkeypatch):
    monkeypatch.setattr(support_module, "build_analysis_llm", lambda: None)
    service = CitationSupportService(
        llm_client=None,
        nli_checker=_FakeNLI(),
        arxiv_adapter=_NoopAdapter(),
    )
    inputs = [
        CitationSupportInput(
            reference=_reference(),
            citation_sentence="The method improves F1 by 7.2%.",
            full_text="The method improves F1 by 7.2% on the test set.",
        ),
        CitationSupportInput(
            reference=_reference(arxiv_id=None, external_id="paper:2"),
            citation_sentence="The method improves F1 by 7.2%.",
        ),
    ]

    batch = service.check_batch(inputs)

    assert batch.summary == {
        "total": 2,
        "supported": 1,
        "partially_supported": 0,
        "unsupported": 0,
        "uncertain": 1,
    }
