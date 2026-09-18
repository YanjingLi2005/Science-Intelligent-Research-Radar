"""LLM-as-reranker fused on top of the hybrid retrieval ranking."""

import json
from types import SimpleNamespace

from sqlalchemy import select

import radar.services.weekly_radar_service as weekly_module
from radar.models import ModelRun, ResearchCase, ScanRun, Source, SourceSnapshot
from radar.schemas import RerankBatchOutput, RerankCandidateScore
from radar.services.weekly_radar_service import WeeklyRadarService

from test_weekly_radar import OnePaperSearch, ScriptedLLM, _settings


STATEMENT = "RadarNet improves exact match over BM25 on DomainQA."


class FixedRetrieval:
    """Deterministic retrieval stand-in: fixed source scores per snapshot."""

    def __init__(self, source_scores, route_score=0.5):
        self.source_scores = dict(source_scores)
        self.route_score = route_score

    def rank_sources(self, query, top_k, snapshot_ids):
        return [
            (snapshot_id, score, "ranked")
            for snapshot_id, score in self.source_scores.items()
            if snapshot_id in snapshot_ids
        ]

    def route_claims(self, snapshot_id, claim_revisions, top_k, **kwargs):
        return [(claim.id, self.route_score, "route") for claim in claim_revisions][
            :top_k
        ]


class RerankLLM(ScriptedLLM):
    """ScriptedLLM that also answers the retrieval_rerank stage by title."""

    def __init__(self, scores_by_title, **kwargs):
        super().__init__(**kwargs)
        self.scores_by_title = scores_by_title
        self.rerank_calls = 0

    def generate_structured(self, *, stage, prompt, response_model, max_tokens=None):
        if stage == "retrieval_rerank":
            self.stages.append(stage)
            self.prompts.append(prompt)
            self.rerank_calls += 1
            payload = json.loads(prompt.split("INPUT JSON:\n", 1)[1])
            return RerankBatchOutput(
                scores=[
                    RerankCandidateScore(
                        key=candidate["key"],
                        score=self.scores_by_title[candidate["title"]],
                        reason="scripted",
                    )
                    for candidate in payload["candidates"]
                ]
            )
        return super().generate_structured(
            stage=stage, prompt=prompt, response_model=response_model
        )


class FailingRerankLLM(ScriptedLLM):
    """The rerank stage is unavailable (unconfigured or failing LLM)."""

    def generate_structured(self, *, stage, prompt, response_model, max_tokens=None):
        if stage == "retrieval_rerank":
            raise RuntimeError("llm_not_configured")
        return super().generate_structured(
            stage=stage, prompt=prompt, response_model=response_model
        )


def _claims():
    return [SimpleNamespace(id="rev-1", statement=STATEMENT, centrality="core")]


def _create_case_and_scan(db_session_factory):
    """ModelRun has FKs to research_cases/scan_runs: create minimal rows."""

    with db_session_factory() as session:
        session.add(
            ResearchCase(
                id="case-x", title="RadarNet", research_question=STATEMENT
            )
        )
        session.add(ScanRun(id="scan-x", case_id="case-x", mode="test", status="running"))
        session.commit()


def _service_with_snapshots(db_session_factory, llm, source_scores):
    service = WeeklyRadarService(
        db_session_factory,
        search_adapter=OnePaperSearch(),
        llm_client=llm,
        settings=_settings(),
    )
    service.retrieval = FixedRetrieval(source_scores)
    with db_session_factory() as session:
        for snapshot_id in source_scores:
            source_id = f"src-{snapshot_id}"
            session.add(
                Source(
                    id=source_id,
                    external_id=f"ext-{snapshot_id}",
                    title=f"Paper {snapshot_id}",
                    authors_json=["Independent Lab"],
                    url=f"https://example.org/{snapshot_id}",
                    integrity_state="normal",
                )
            )
            session.flush()
            session.add(
                SourceSnapshot(
                    id=snapshot_id,
                    source_id=source_id,
                    version_label="v1",
                    title=f"Paper {snapshot_id}",
                    abstract=f"Abstract of paper {snapshot_id}.",
                    content_text=f"Abstract of paper {snapshot_id}.",
                    content_hash=f"hash-{snapshot_id}",
                )
            )
        session.commit()
    return service


def _rank(service, source_scores, analysis_limit=1, stats=None):
    return service._rank_pairs(
        list(source_scores),
        _claims(),
        analysis_limit,
        query="DomainQA RadarNet",
        case_id="case-x",
        scan_run_id="scan-x",
        stats=stats,
    )


def test_llm_rerank_changes_top_k_order_and_records_model_run(db_session_factory):
    source_scores = {"s1": 1.0, "s2": 0.9, "s3": 0.8}
    llm = RerankLLM({"Paper s1": 0, "Paper s2": 0, "Paper s3": 10})
    service = _service_with_snapshots(db_session_factory, llm, source_scores)
    _create_case_and_scan(db_session_factory)
    stats = {}

    pairs = _rank(service, source_scores, stats=stats)

    # The hybrid ranking put s1 first; the LLM overrules it with s3.
    assert [snapshot_id for snapshot_id, _, _ in pairs] == ["s3"]
    assert stats == {"llm_rerank": True, "llm_rerank_rank_changes": 3}
    assert llm.rerank_calls == 1
    with db_session_factory() as session:
        run = session.scalar(
            select(ModelRun).where(ModelRun.stage == "retrieval_rerank")
        )
        assert run is not None
        assert run.case_id == "case-x"
        assert run.scan_run_id == "scan-x"
        assert run.input_refs_json[0] == "rev-1"
        assert set(run.input_refs_json[1:]) == {"s1", "s2", "s3"}
        assert run.parsed_output_json["scores"]


def test_hybrid_score_corrects_mild_llm_preference(db_session_factory):
    source_scores = {"s1": 1.0, "s2": 0.55}
    # The LLM slightly prefers s2, but s1's much stronger hybrid score wins.
    llm = RerankLLM({"Paper s1": 5, "Paper s2": 6})
    service = _service_with_snapshots(db_session_factory, llm, source_scores)
    _create_case_and_scan(db_session_factory)
    stats = {}

    pairs = _rank(service, source_scores, stats=stats)

    assert [snapshot_id for snapshot_id, _, _ in pairs] == ["s1"]
    assert stats["llm_rerank"] is True


def test_rerank_failure_keeps_hybrid_order_without_model_run(db_session_factory):
    source_scores = {"s1": 1.0, "s2": 0.9, "s3": 0.8}
    service = _service_with_snapshots(
        db_session_factory, FailingRerankLLM(), source_scores
    )
    stats = {}

    pairs = _rank(service, source_scores, stats=stats)

    # Baseline: no LLM client at all yields the untouched hybrid order.
    baseline = WeeklyRadarService(
        db_session_factory,
        search_adapter=OnePaperSearch(),
        llm_client=FailingRerankLLM(),
        settings=_settings(),
    )
    baseline.retrieval = FixedRetrieval(source_scores)
    baseline.llm_client = None
    expected = _rank(baseline, source_scores)
    assert pairs == expected
    assert stats == {"llm_rerank": False}
    with db_session_factory() as session:
        run = session.scalar(
            select(ModelRun).where(ModelRun.stage == "retrieval_rerank")
        )
        assert run is None


def test_rerank_scores_candidates_in_batches(db_session_factory, monkeypatch):
    monkeypatch.setattr(weekly_module, "_RERANK_BATCH_SIZE", 2)
    source_scores = {f"s{index}": 1.0 - 0.05 * index for index in range(1, 7)}
    llm = RerankLLM({f"Paper s{index}": 5 for index in range(1, 7)})
    service = _service_with_snapshots(db_session_factory, llm, source_scores)
    _create_case_and_scan(db_session_factory)
    stats = {}

    pairs = _rank(service, source_scores, analysis_limit=2, stats=stats)

    # Six head candidates against one claim, batches of two: three calls.
    assert llm.rerank_calls == 3
    assert stats["llm_rerank"] is True
    # Uniform LLM scores leave the hybrid order intact.
    assert [snapshot_id for snapshot_id, _, _ in pairs] == ["s1", "s2"]


def test_full_scan_records_llm_rerank_stats(db_session_factory, golden_case):
    llm = RerankLLM({"A Matched Re-evaluation of RadarNet": 9})
    scan_id = WeeklyRadarService(
        db_session_factory,
        search_adapter=OnePaperSearch(),
        llm_client=llm,
        settings=_settings(),
    ).run(
        golden_case,
        query="DomainQA RadarNet exact match",
        max_results=5,
        analysis_limit=1,
    )

    with db_session_factory() as session:
        scan = session.get(ScanRun, scan_id)
        run = session.scalar(
            select(ModelRun).where(
                ModelRun.stage == "retrieval_rerank",
                ModelRun.scan_run_id == scan_id,
            )
        )
        assert scan.status == "completed"
        assert scan.stats_json["llm_rerank"] is True
        assert scan.stats_json["llm_rerank_rank_changes"] == 0
        assert scan.stats_json["impact_candidates"] == 1
        assert run is not None
        assert run.case_id == golden_case
