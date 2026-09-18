"""Tests for Snapshot Condition Cache and Pre-Impact Gate (Pre-Filter)."""

from datetime import datetime, timezone
import uuid

import pytest
from sqlalchemy import select

from radar.db import session_scope
from radar.models import (
    Claim,
    ClaimRevision,
    ClaimSourceLink,
    ManuscriptVersion,
    ResearchCase,
    ScanRun,
    Source,
    SourceSnapshot,
)
from radar.schemas import EmpiricalClaimContract, ImpactAssessmentOutput, IncomingResult
from radar.services.impact_service import ImpactService
from radar.services.weekly_radar_service import WeeklyRadarService
from tests.test_weekly_radar import OnePaperSearch, ScriptedLLM, _settings


def test_pre_filter_relevance_zero_route_score():
    is_rel, reason = ImpactService.pre_filter_relevance(
        claim_statement="RadarNet achieves 86.4% exact match on DomainQA.",
        claim_contract={"dataset": "DomainQA", "task": "open-domain QA"},
        snapshot_title="Some Paper",
        snapshot_abstract="Some abstract",
        route_score=0.0,
    )
    assert not is_rel
    assert reason == "route_score_zero"


def test_pre_filter_relevance_rejects_irrelevant_pair():
    is_rel, reason = ImpactService.pre_filter_relevance(
        claim_statement="RadarNet achieves 86.4% exact match on DomainQA under unseen domain shifts.",
        claim_contract={"dataset": "DomainQA", "task": "open-domain QA", "metric": "exact match"},
        snapshot_title="Urban traffic congestion prediction using spatial graph networks",
        snapshot_abstract="We study highway vehicular speeds in urban road networks.",
        snapshot_text="Vehicular traffic flow analysis on highway sensor datasets.",
        route_score=0.08,
    )
    assert not is_rel
    assert reason == "no_semantic_overlap"


def test_pre_filter_relevance_accepts_dataset_match():
    is_rel, reason = ImpactService.pre_filter_relevance(
        claim_statement="RadarNet achieves 86.4% exact match on DomainQA.",
        claim_contract={"dataset": "DomainQA", "task": "QA"},
        snapshot_title="Evaluating DomainQA benchmarks with dense retrievers",
        snapshot_abstract="We report results on the DomainQA benchmark.",
        route_score=0.10,
    )
    assert is_rel
    assert reason == "dataset_match"


def test_pre_filter_relevance_accepts_keyword_overlap():
    is_rel, reason = ImpactService.pre_filter_relevance(
        claim_statement="Robustness of retrieval-augmented generation under noise",
        claim_contract={"task": "retrieval generation"},
        snapshot_title="Benchmarking retrieval augmented generation models against noise",
        snapshot_abstract="We analyze hallucination and noise in RAG systems.",
        route_score=0.15,
    )
    assert is_rel
    assert reason == "passed"


def test_snapshot_condition_cache_shared_across_multiple_claims(db_session_factory, golden_case):
    """Verify that when 2 claims match the same snapshot, incoming_result LLM is called only ONCE."""
    with session_scope(db_session_factory) as session:
        manuscript = session.scalar(
            select(ManuscriptVersion).where(
                ManuscriptVersion.case_id == golden_case,
                ManuscriptVersion.is_current.is_(True),
            )
        )
        c2 = Claim(
            id=f"claim-two-{uuid.uuid4().hex[:6]}",
            case_id=golden_case,
            stable_key="claim-key-2",
            lifecycle_state="active",
        )
        session.add(c2)
        session.flush()

        cr2 = ClaimRevision(
            id=f"rev-two-{uuid.uuid4().hex[:6]}",
            claim_id=c2.id,
            manuscript_version_id=manuscript.id,
            revision_no=1,
            statement="BM25 baseline fails to generalize across domains.",
            centrality="major",
            falsifiable_condition="Domain transfer accuracy drops below 50%.",
            source_locator="sec:results:p:2",
            source_quote="BM25 dropped to 42.1% on the cross-domain transfer split.",
            review_state="confirmed",
            contract_json=EmpiricalClaimContract(
                task="open-domain QA",
                dataset="DomainQA",
                comparator="BM25",
            ).model_dump(),
        )
        session.add(cr2)
        session.commit()

    llm = ScriptedLLM()
    service = WeeklyRadarService(
        db_session_factory,
        search_adapter=OnePaperSearch(),
        llm_client=llm,
        settings=_settings(),
    )

    scan_id = service._start_scan(golden_case, "DomainQA RadarNet exact match", 5, 5, mode="live")
    records = OnePaperSearch().search(golden_case, None)
    snapshot_id = service._store_records(records)[0]

    # Assess pair with claim 1
    impact_1 = service._assess_pair(
        scan_id=scan_id,
        case_id=golden_case,
        manuscript=manuscript,
        snapshot_id=snapshot_id,
        revision_id="claim-rev-01",
        route_score=0.8,
    )
    assert impact_1 is not None
    assert service.snapshot_cache_size == 1
    assert service.fidelity["snapshot_cache_hits"] == 0
    assert service.fidelity["snapshot_cache_misses"] == 1

    # Assess pair with claim 2: snapshot condition must hit cache without calling LLM!
    impact_2 = service._assess_pair(
        scan_id=scan_id,
        case_id=golden_case,
        manuscript=manuscript,
        snapshot_id=snapshot_id,
        revision_id=cr2.id,
        route_score=0.8,
    )
    assert impact_2 is not None
    assert service.snapshot_cache_size == 1
    assert service.fidelity["snapshot_cache_hits"] == 1
    assert service.fidelity["snapshot_cache_misses"] == 1

    # Total LLM calls for incoming_result across both claims MUST BE EXACTLY 1
    incoming_result_calls = [s for s in llm.stages if s == "incoming_result"]
    assert len(incoming_result_calls) == 1

    # Verify clear_snapshot_cache
    service.clear_snapshot_cache()
    assert service.snapshot_cache_size == 0


def test_pre_filter_gate_skips_assessment_in_assess_pair(db_session_factory, golden_case):
    """Verify that pre_filter_relevance skips calling LLM when pair is completely irrelevant."""
    with session_scope(db_session_factory) as session:
        manuscript = session.scalar(
            select(ManuscriptVersion).where(
                ManuscriptVersion.case_id == golden_case,
                ManuscriptVersion.is_current.is_(True),
            )
        )
        # Create an unrelated source snapshot
        source = Source(
            id=f"src-irrel-{uuid.uuid4().hex[:6]}",
            title="Highway traffic prediction using spatio-temporal graphs",
            source_kind="arxiv",
            external_id="arxiv:traffic-irrel",
            url="https://arxiv.org/abs/traffic-irrel",
            created_at=datetime.now(timezone.utc),
        )
        session.add(source)
        session.flush()
        snapshot = SourceSnapshot(
            id=f"snap-irrel-{uuid.uuid4().hex[:6]}",
            source_id=source.id,
            version_label="v1",
            content_hash="hash-irrel-123",
            title=source.title,
            abstract="Vehicular traffic sensor velocity predictions on highway networks.",
            content_text="Vehicular traffic sensor velocity predictions on highway networks.",
            created_at=datetime.now(timezone.utc),
        )
        session.add(snapshot)
        session.commit()

    llm = ScriptedLLM()
    service = WeeklyRadarService(
        db_session_factory,
        search_adapter=OnePaperSearch(),
        llm_client=llm,
        settings=_settings(),
    )
    scan_id = service._start_scan(golden_case, "query", 5, 5, mode="live")

    # Low route score (0.05) and zero lexical/task overlap with claim-rev-01 (QA/DomainQA)
    impact = service._assess_pair(
        scan_id=scan_id,
        case_id=golden_case,
        manuscript=manuscript,
        snapshot_id=snapshot.id,
        revision_id="claim-rev-01",
        route_score=0.05,
    )
    assert impact is None
    assert service.fidelity["pre_filter_skipped"] == 1
    assert len(llm.stages) == 0  # Zero LLM calls made!
