"""English arXiv query generation for profile-less cases."""

from types import SimpleNamespace

from sqlalchemy import select

from radar.config import Settings
from radar.models import ResearchCase, ScanRun
from radar.schemas import (
    EvidenceSpan,
    ImpactAssessmentOutput,
    IncomingResult,
    SearchQueryBatch,
    SourceRecord,
)
from radar.services.manuscript_understanding_service import (
    ManuscriptUnderstandingService,
)
from radar.services.weekly_radar_service import WeeklyRadarService


INCOMING_QUOTE = (
    "On the DomainQA unseen-domain split, NewRAG reaches 70.1 exact match while "
    "RadarNet reaches 64.0 using the same BM25 baseline."
)
OWN_QUOTE = (
    "On DomainQA, RadarNet improves exact match from 61.2% to 68.7% over BM25 "
    "under unseen-domain evaluation."
)


def _settings():
    return Settings(
        _env_file=None,
        llm_provider="deepseek",
        llm_api_key="sk-test-key-000000000000",
        llm_model="deepseek-v4-pro",
        llm_base_url="https://api.deepseek.com",
    )


class QueryLLM:
    """LLM double scripted for query-generation and analysis stages."""

    def __init__(self, queries=None, claim_queries=None, fail=False):
        self.queries = queries or []
        self.claim_queries = claim_queries
        self.fail = fail
        self.calls = 0
        self.claim_calls = 0

    def generate_structured(self, *, stage, prompt, response_model, max_tokens=None):
        if stage == "claim_search_query_generation":
            self.claim_calls += 1
            if self.fail:
                raise RuntimeError("llm_not_configured")
            return SearchQueryBatch(
                queries=self.claim_queries if self.claim_queries is not None else self.queries
            )
        if stage == "search_query_generation":
            self.calls += 1
            if self.fail:
                raise RuntimeError("llm_not_configured")
            return SearchQueryBatch(queries=self.queries)
        if stage == "incoming_result":
            return IncomingResult(
                task="open-domain QA",
                dataset="DomainQA",
                split="unseen-domain",
                metric="exact match",
                comparator="BM25",
                scope="RadarNet",
                direction="challenges",
            )
        if stage == "impact_assessment":
            return ImpactAssessmentOutput(
                stance="challenges",
                impact_mode="boundary_condition",
                comparability="compatible",
                condition_differences=[],
                evidence_own=EvidenceSpan(quote=OWN_QUOTE, locator="sec:main-results:p:1"),
                evidence_new=EvidenceSpan(quote=INCOMING_QUOTE, locator="paragraph:1"),
                change_depth=3,
                suggested_action="narrow_claim",
                uncertainty_sources=[],
            )
        raise AssertionError(f"unexpected stage: {stage}")


class RecordingSearch:
    def __init__(self):
        self.queries = []

    def search(self, case_id, watch_query):
        self.queries.append(watch_query.query)
        return [
            SourceRecord(
                external_id="arxiv:test-query-1",
                title="A Matched Re-evaluation of RadarNet",
                authors=["Independent Lab"],
                abstract=INCOMING_QUOTE,
                url="https://arxiv.org/abs/test-query-1",
                published_at="2026-07-20T00:00:00Z",
                arxiv_id="test-query-1",
            )
        ]


def _service(db_session_factory, llm, search_adapter=None):
    return WeeklyRadarService(
        db_session_factory,
        search_adapter=search_adapter or RecordingSearch(),
        llm_client=llm,
        settings=_settings(),
    )


def test_sanitize_search_query_strips_numbering_quotes_and_arxiv_syntax():
    sanitize = WeeklyRadarService._sanitize_search_query
    assert sanitize('1. "multimodal RAG accuracy"') == "multimodal RAG accuracy"
    assert sanitize("2) agentic RAG AND dynamic tool orchestration") == (
        "agentic RAG dynamic tool orchestration"
    )
    assert sanitize("ti:retrieval (benchmark) OR reranker") == "retrieval benchmark reranker"
    assert sanitize("- all:long-context ANDNOT quantization\nextra line") == (
        "long-context quantization"
    )
    assert sanitize("   ") == ""
    # Word and character caps keep the query short and plain.
    long_query = " ".join(f"term{index}" for index in range(20))
    sanitized = sanitize(long_query)
    assert len(sanitized.split()) == 10
    assert len(sanitize("x" * 200)) <= 120


def test_claim_queries_are_used_cleaned_and_cached(db_session_factory, golden_case):
    llm = QueryLLM(
        claim_queries=[
            '1. "RadarNet exact match under domain shift"',
            "ti:claim-aware reranking OR rivals",
            "RadarNet retrieval latency benchmark",
        ]
    )
    service = _service(db_session_factory, llm)

    queries = service.suggested_queries(golden_case)

    assert queries == [
        "RadarNet exact match under domain shift",
        "claim-aware reranking rivals",
        "RadarNet retrieval latency benchmark",
    ]
    assert llm.claim_calls > 0
    assert llm.calls == 0
    with db_session_factory() as session:
        research_case = session.get(ResearchCase, golden_case)
        cached = research_case.settings_json["claim_search_queries"]
        assert cached["queries"] == queries

    # Second call is served from the cache without another LLM round-trip.
    assert service.suggested_queries(golden_case) == queries
    assert llm.claim_calls > 0
    assert llm.calls == 0


def test_llm_failure_falls_back_to_research_question(db_session_factory, golden_case):
    llm = QueryLLM(fail=True)
    service = _service(db_session_factory, llm)

    with db_session_factory() as session:
        question = session.get(ResearchCase, golden_case).research_question
    assert service.suggested_queries(golden_case) == [question]
    # Failures are not cached, so a later working LLM can still succeed.
    with db_session_factory() as session:
        research_case = session.get(ResearchCase, golden_case)
        assert "generated_search_queries" not in research_case.settings_json


def test_empty_llm_output_falls_back_to_research_question(db_session_factory, golden_case):
    llm = QueryLLM(["", "   "])
    service = _service(db_session_factory, llm)

    with db_session_factory() as session:
        question = session.get(ResearchCase, golden_case).research_question
    assert service.suggested_queries(golden_case) == [question]


def test_blank_research_question_falls_back_to_confirmed_claims(
    db_session_factory, golden_case
):
    """An optional blank question must not fail an auto scan with
    ``watch_query_required`` when the project has confirmed claims."""
    with db_session_factory() as session:
        session.get(ResearchCase, golden_case).research_question = ""
        session.commit()

    service = _service(db_session_factory, QueryLLM(fail=True))

    queries = service.suggested_queries(golden_case)

    assert queries
    assert all(query.strip() for query in queries)
    assert any("RadarNet" in query for query in queries)


def test_profile_watch_topics_still_win_when_claim_queries_unavailable(
    db_session_factory, golden_case, monkeypatch
):
    profile = SimpleNamespace(
        watch_topics=["RAG robustness evaluation", "domain shift calibration"],
        terminology=None,
        claim_profiles=[],
    )
    monkeypatch.setattr(
        ManuscriptUnderstandingService,
        "latest_profile",
        classmethod(lambda cls, case_id, session_factory=None: profile),
    )
    llm = QueryLLM(claim_queries=[], queries=["should not be used"])
    service = _service(db_session_factory, llm)

    assert service.suggested_queries(golden_case) == [
        "RAG robustness evaluation",
        "domain shift calibration",
    ]
    assert llm.calls == 0


def test_auto_scan_records_llm_queries_in_scan_stats(db_session_factory, golden_case):
    search = RecordingSearch()
    llm = QueryLLM(
        claim_queries=[
            "RadarNet robust retrieval evaluation",
            "domain shift RAG benchmark",
            "hierarchical routing latency comparison",
        ]
    )
    service = _service(db_session_factory, llm, search_adapter=search)

    scan_id = service.run_auto(golden_case, max_results=10, analysis_limit=1)

    with db_session_factory() as session:
        scan = session.get(ScanRun, scan_id)
        assert scan.status == "completed"
        assert scan.stats_json["search_queries"] == [
            "RadarNet robust retrieval evaluation",
            "domain shift RAG benchmark",
            "hierarchical routing latency comparison",
        ]
    assert search.queries == [
        "RadarNet robust retrieval evaluation",
        "domain shift RAG benchmark",
        "hierarchical routing latency comparison",
    ]
