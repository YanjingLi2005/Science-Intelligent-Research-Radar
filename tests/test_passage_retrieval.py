from radar.schemas import CitationSupportInput, EvidenceSpan, ReferenceMatch
from radar.services.citation_support_service import CitationSupportService
from radar.services import citation_support_service as support_module
from radar.services.passage_retrieval import PassageRetrievalService


class _FailingEmbedding:
    def embed(self, texts):
        raise RuntimeError("embedding unavailable")


class _EntailmentReranker:
    def check(self, claim, evidence):
        score = 0.99 if "benchmark accuracy" in evidence else 0.0
        return {"label": "entailment", "scores": {"entailment": score}}


class _InjectedRetrieval:
    def __init__(self, evidence):
        self.evidence = evidence
        self.calls = []

    def retrieve(self, query, content, top_k=3):
        self.calls.append((query, content, top_k))
        return [self.evidence]


class _FakeNLI:
    def check(self, claim, evidence):
        return {"label": "entailment", "scores": {"entailment": 0.95}}


def _reference() -> ReferenceMatch:
    return ReferenceMatch(
        source_kind="arxiv",
        external_id="arxiv:1234.5678",
        title="A Test Paper",
        arxiv_id="1234.5678",
    )


def test_decimal_sentence_is_split_without_breaking_numeric_sentence():
    content = (
        "The baseline score was 0.91. "
        "The proposed method reaches 0.97 on DatasetX. "
        "The appendix describes implementation details."
    )

    spans = PassageRetrievalService().retrieve(
        "method reaches 0.97", content, top_k=1
    )

    assert spans[0].quote == "The proposed method reaches 0.97 on DatasetX."
    assert spans[0].locator == "sentence:2"


def test_bm25_and_lexical_ranking_put_relevant_passage_first():
    content = (
        "The system uses a retrieval index for document lookup.\n"
        "The proposed calibration improves retrieval quality on held-out data.\n"
        "The appendix lists implementation details and hardware settings."
    )

    spans = PassageRetrievalService().retrieve(
        "calibration retrieval", content, top_k=2
    )

    assert spans[0].quote.startswith("The proposed calibration improves retrieval")
    assert spans[0].locator == "paragraph:2"


def test_embedding_failure_still_returns_lexical_passages():
    content = "Calibration improves retrieval quality.\nUnrelated hardware details."

    spans = PassageRetrievalService(embedding_client=_FailingEmbedding()).retrieve(
        "calibration retrieval", content, top_k=2
    )

    assert len(spans) == 2
    assert spans[0].quote == "Calibration improves retrieval quality."


def test_cross_encoder_reranks_head_by_entailment():
    content = (
        "The method performance discussion covers several implementation details.\n"
        "The method improves benchmark accuracy on the held-out test set."
    )

    spans = PassageRetrievalService(
        cross_encoder=_EntailmentReranker()
    ).retrieve("method performance", content, top_k=1)

    assert spans[0].quote == (
        "The method improves benchmark accuracy on the held-out test set."
    )


def test_citation_support_uses_injected_passage_retrieval(monkeypatch):
    monkeypatch.setattr(support_module, "build_analysis_llm", lambda: None)
    evidence = EvidenceSpan(
        quote="The method improves F1 by 7.2% on the test set.",
        locator="paragraph:4",
        source_snapshot_id="",
    )
    retrieval = _InjectedRetrieval(evidence)
    service = CitationSupportService(
        llm_client=None,
        nli_checker=_FakeNLI(),
        passage_retrieval=retrieval,
    )
    citation = CitationSupportInput(
        reference=_reference(),
        citation_sentence="The method improves F1 by 7.2%.",
        full_text="Some source text.",
    )

    result = service.check(citation)

    assert result.category == "supported"
    assert result.evidence_quote == evidence.quote
    assert retrieval.calls[0][2] == 5
