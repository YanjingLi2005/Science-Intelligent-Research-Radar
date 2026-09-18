"""Deep-research service: planning, search, compression, synthesis, persistence."""

from datetime import datetime, timezone

from sqlalchemy import select

from radar.config import Settings
from radar.models import ScanRun, Source, SourceSnapshot
from radar.schemas import (
    DeepResearchBrief,
    DeepResearchPlan,
    DeepResearchSection,
    DeepResearchSourceSummary,
    DeepResearchSubQuery,
    SourceRecord,
)
from starlette.testclient import TestClient

from radar.api import app
from radar.services.deep_research_service import DeepResearchService
from radar.services.scan_runner import start_deep_research


def _settings():
    return Settings(
        _env_file=None,
        llm_provider="deepseek",
        llm_api_key="sk-test-key-000000000000",
        llm_model="deepseek-v4-pro",
        llm_base_url="https://api.deepseek.com",
    )


class TwoPaperSearch:
    source_kind = "arxiv"

    def search(self, case_id, watch_query):
        return [
            SourceRecord(
                external_id="arxiv:deep-research-1",
                title="Retrieval-Augmented Generation for Science",
                authors=["Alice", "Bob"],
                abstract=(
                    "We study retrieval-augmented generation for scientific "
                    "literature and show that reranking improves answer quality."
                ),
                url="https://arxiv.org/abs/deep-research-1",
                published_at="2026-07-01T00:00:00Z",
                arxiv_id="deep-research-1",
            ),
            SourceRecord(
                external_id="arxiv:deep-research-2",
                title="Benchmarking Language Models on Scientific Claims",
                authors=["Carol"],
                abstract=(
                    "We introduce a benchmark for verifying scientific claims "
                    "against new evidence and report baseline results."
                ),
                url="https://arxiv.org/abs/deep-research-2",
                published_at="2026-06-15T00:00:00Z",
                arxiv_id="deep-research-2",
            ),
        ]


class ScriptedDeepLLM:
    last_receipt = {
        "raw_response": "{}",
        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        "latency_ms": 3,
    }

    def __init__(self, *, fail_synthesis=False):
        self.fail_synthesis = fail_synthesis

    def generate_structured(self, *, stage, prompt, response_model, max_tokens=None):
        if stage == "deep_research_plan":
            return DeepResearchPlan(
                title="RAG for Science",
                sub_queries=[
                    DeepResearchSubQuery(
                        query="retrieval augmented generation",
                        rationale="core method",
                        focus="methods",
                    )
                ],
            )
        if stage == "deep_research_source_summary":
            return DeepResearchSourceSummary(
                title="",
                authors=[],
                year="2026",
                venue="arXiv",
                url="http://example.com",
                relevance="Directly relevant to the question",
                key_findings=["Reranking helps QA", "Benchmark released"],
                limitations=[],
                contradictions=[],
            )
        if stage == "deep_research_synthesis":
            if self.fail_synthesis:
                raise RuntimeError("synthesis failed")
            return DeepResearchBrief(
                title="Deep Brief",
                executive_summary="A summary of the evidence.",
                sections=[
                    DeepResearchSection(
                        heading="Main findings",
                        findings=["Finding one"],
                        source_ids=[],
                    )
                ],
                key_insights=["Insight"],
                contradictions=[],
                research_gaps=["Gap"],
                recommended_next_steps=["Run a radar scan"],
                sources=[],
            )
        raise AssertionError(f"unexpected stage: {stage}")


def test_deep_research_run_completes_and_persists_brief(
    db_session_factory, golden_case
):
    service = DeepResearchService(
        db_session_factory,
        search_adapters=[TwoPaperSearch()],
        llm_client=ScriptedDeepLLM(),
        settings=_settings(),
    )

    with db_session_factory() as session:
        before_sources = len(list(session.scalars(select(Source))))
        before_snapshots = len(list(session.scalars(select(SourceSnapshot))))

    scan_id = service.run(
        golden_case,
        question="How does retrieval-augmented generation help science?",
        depth=1,
        max_sources=4,
        max_subqueries=2,
    )

    with db_session_factory() as session:
        scan = session.get(ScanRun, scan_id)
        assert scan is not None
        assert scan.status == "completed"
        assert scan.mode == "deep_research"
        stats = scan.stats_json or {}
        assert stats["scanned_papers"] == 2
        assert stats["sub_queries"] == ["retrieval augmented generation"]
        brief = stats["research_brief"]
        assert brief["title"] == "Deep Brief"
        assert brief["executive_summary"] == "A summary of the evidence."

        # The external papers are stored as normal sources/snapshots.
        sources = list(session.scalars(select(Source)))
        snapshots = list(session.scalars(select(SourceSnapshot)))
        assert len(sources) == before_sources + 2
        assert len(snapshots) == before_snapshots + 2


def test_deep_research_fallback_synthesis_when_llm_fails(
    db_session_factory, golden_case
):
    service = DeepResearchService(
        db_session_factory,
        search_adapters=[TwoPaperSearch()],
        llm_client=ScriptedDeepLLM(fail_synthesis=True),
        settings=_settings(),
    )

    scan_id = service.run(
        golden_case,
        question="test",
        depth=1,
        max_sources=4,
        max_subqueries=2,
    )

    with db_session_factory() as session:
        scan = session.get(ScanRun, scan_id)
        assert scan.status == "completed"
        brief = (scan.stats_json or {})["research_brief"]
        assert "deep_research_synthesis_fallback" in brief["warnings"]
        assert brief["sources"], "fallback brief should carry the compressed sources"
        assert brief["sources"][0]["source_id"]


def test_scan_runner_start_deep_research_uses_shared_guard(
    db_session_factory, golden_case
):
    class StubDeepService:
        def __init__(self, session_factory):
            self.session_factory = session_factory
            self.called = False

        def run(self, case_id, *, scan_id=None, progress_callback=None, cancel_check=None, **kwargs):
            self.called = True
            with self.session_factory() as session:
                scan = session.get(ScanRun, scan_id)
                assert scan is not None
                scan.status = "completed"
                scan.finished_at = datetime.now(timezone.utc)
                scan.stats_json = {
                    **scan.stats_json,
                    "research_brief": {"ok": True},
                    "progress": {"value": 1.0, "message": "done"},
                }
                session.commit()
            return scan_id

    stub = StubDeepService(db_session_factory)
    scan_id = start_deep_research(
        golden_case,
        question="test",
        session_factory=db_session_factory,
        service_factory=lambda factory: stub,
    )

    from radar.services.scan_runner import wait_for_scan

    assert wait_for_scan(scan_id, timeout=10)
    assert stub.called
    with db_session_factory() as session:
        scan = session.get(ScanRun, scan_id)
        assert scan.mode == "deep_research"
        assert scan.stats_json["research_brief"] == {"ok": True}


def test_deep_research_api_route_launches_runner(tmp_path, monkeypatch):
    import radar.api as api_module

    monkeypatch.setattr(
        api_module,
        "start_deep_research",
        lambda case_id, **kwargs: "deep-scan-123",
    )

    with TestClient(app) as client:
        resp = client.post(
            "/api/cases/does-not-matter/deep-research",
            json={"question": "test", "depth": 1, "max_sources": 4},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json() == {
            "scan_id": "deep-scan-123",
            "status": "running",
            "mode": "deep_research",
        }
