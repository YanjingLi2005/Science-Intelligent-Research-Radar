from sqlalchemy import select

from radar.models import ActionItem, ImpactCandidate, ScanRun, SourceSnapshot
from radar.schemas import ActionAdviceOutput
from radar.services.action_service import ActionService
from radar.services.report_service import ReportService
from radar.services.review_service import ReviewService


def _first_snapshot_id(db_session_factory) -> str:
    with db_session_factory() as session:
        return session.scalars(select(SourceSnapshot.id)).first()


def _make_impact(db_session_factory, impact_id, scan_id, claim_revision_id, **overrides):
    payload = dict(
        id=impact_id,
        scan_run_id=scan_id,
        claim_revision_id=claim_revision_id,
        source_snapshot_id=_first_snapshot_id(db_session_factory),
        event_type="paper",
        stance="challenges",
        impact_mode="boundary_condition",
        strategic_flags_json=[],
        comparability="compatible",
        condition_differences_json=[],
        evidence_own_json={"quote": "own", "locator": "sec:p:1"},
        evidence_new_json={"quote": "new", "locator": "abstract:1"},
        change_depth=2,
        severity="review",
        suggested_action="run_comparison",
        uncertainty_json=[],
        review_state="candidate",
        trust_state="verified",
    )
    payload.update(overrides)
    with db_session_factory() as session:
        session.add(ImpactCandidate(**payload))
        session.commit()


def _make_scan(db_session_factory, scan_id, case_id):
    with db_session_factory() as session:
        session.add(
            ScanRun(
                id=scan_id,
                case_id=case_id,
                mode="test",
                status="completed",
                query_json={},
                stats_json={},
            )
        )
        session.commit()


def test_golden_impacts_generate_all_project_action_types(
    db_session_factory, golden_case
):
    service = ActionService(db_session_factory)
    actions = service.list_actions(golden_case)
    action_types = {item.action_type for item in actions}

    assert {
        "team_decision",
        "experiment",
        "writing",
        "cite",
        "competitor_response",
        "revalidation",
    } <= action_types
    assert any(
        item.action_type == "team_decision"
        and item.priority == "critical"
        and item.status == "open"
        for item in actions
    )
    assert any(
        item.action_type == "competitor_response" and item.due_label == "72_hours"
        for item in actions
    )
    assert any(
        item.action_type == "revalidation" and item.status == "open"
        for item in actions
    )


def test_candidate_impacts_create_automatic_attention_states(
    db_session_factory, golden_case
):
    service = ActionService(db_session_factory)

    assert service.claim_attention_state("claim-01") == "disputed"
    assert service.claim_attention_state("claim-03") == "competitor_pressure"
    assert service.claim_attention_state("claim-09") == "revalidation_required"
    assert service.claim_attention_state("claim-10") == "stable"


def test_action_lifecycle_and_impact_dismissal_are_synchronized(
    db_session_factory, golden_case
):
    service = ActionService(db_session_factory)
    competitor = next(
        item
        for item in service.list_actions(golden_case)
        if item.action_type == "competitor_response"
    )
    service.update_status(competitor.id, "in_progress")
    with db_session_factory() as session:
        assert session.get(ActionItem, competitor.id).status == "in_progress"

    ReviewService(db_session_factory).dismiss_impact("impact-03")
    with db_session_factory() as session:
        statuses = set(
            session.scalars(
                select(ActionItem.status).where(
                    ActionItem.impact_candidate_id == "impact-03"
                )
            )
        )
    assert statuses == {"dismissed"}


def test_weekly_action_report_and_writing_brief(
    db_session_factory, golden_case
):
    report_service = ReportService(db_session_factory)
    report = report_service.get_weekly_action_report("scan-demo-week-29")
    brief = report_service.get_writing_brief(golden_case)
    markdown = report_service.export_writing_brief(golden_case)

    assert report["urgent"] >= 3
    assert report["counts_by_type"]["experiment"] >= 1
    assert report["counts_by_type"]["competitor_response"] == 1
    assert report["counts_by_type"]["revalidation"] == 1
    assert len(brief["supports"]) == 2
    assert len(brief["challenges"]) == 2
    assert len(brief["integrity"]) == 1
    assert "# Research Radar — Discussion Evidence Brief" in markdown
    assert "## Counter-evidence" in markdown
    assert "- Venue:" in markdown
    assert "- Original:" in markdown
    assert "- PDF:" in markdown
    assert "- DOI:" in markdown


def test_challenges_with_unknown_conditions_still_generates_data_action(
    db_session_factory, golden_case
):
    _make_impact(
        db_session_factory,
        "impact-test-data",
        "scan-demo-week-29",
        "claim-rev-02",
        stance="challenges",
        comparability="partial",
        condition_differences_json=[
            {
                "field": "dataset",
                "own_value": "DomainQA",
                "incoming_value": None,
                "status": "unknown",
                "explanation": "At least one source does not report this condition.",
            }
        ],
    )
    service = ActionService(db_session_factory)
    service.sync_impact_actions("impact-test-data")

    data_actions = [
        item
        for item in service.list_actions(golden_case)
        if item.claim_revision_id == "claim-rev-02" and item.action_type == "data"
    ]
    assert len(data_actions) == 1
    assert "可比性信息" in data_actions[0].title


def test_llm_advice_replaces_rule_templates(db_session_factory, golden_case):
    _make_impact(
        db_session_factory,
        "impact-test-llm",
        "scan-demo-week-29",
        "claim-rev-06",
        stance="supports",
        impact_mode="replication",
        severity="informative",
        suggested_action="cite",
    )
    advice = ActionAdviceOutput(
        category="cite",
        title="在 related work 引用该独立复现结果",
        rationale="该工作独立复现了 C6 的消融结论，去掉 claim-aware reranking 后 EM 下降 5.2 点，与你报告的 5.6 点一致；建议引用以增强可信度。",
        checklist=["加入精确引用", "在消融讨论中说明一致性"],
    )
    service = ActionService(db_session_factory)
    action_ids = service.sync_impact_actions("impact-test-llm", advice=advice)

    with db_session_factory() as session:
        action = session.get(ActionItem, action_ids[0])
        assert action is not None
        assert action.action_type == "cite"
        assert action.advice_source == "llm"
        assert action.title == "在 related work 引用该独立复现结果"
        assert action.checklist_json == ["加入精确引用", "在消融讨论中说明一致性"]
        assert action.priority == "low"
        assert action.due_label == "before_next_draft"


def test_multiple_impacts_merge_into_one_action_per_claim_and_type(
    db_session_factory, golden_case
):
    service = ActionService(db_session_factory)
    for impact_id, severity in [("impact-merge-a", "informative"), ("impact-merge-b", "review")]:
        _make_impact(
            db_session_factory,
            impact_id,
            "scan-demo-week-29",
            "claim-rev-06",
            stance="neutral",
            impact_mode="method_substitution",
            severity=severity,
        )
    service.sync_impact_actions(
        "impact-merge-a",
        advice=ActionAdviceOutput(
            category="experiment",
            title="做等预算方法对比",
            rationale="论文甲提出可替代模块。",
            checklist=["步骤甲一", "共同步骤"],
        ),
    )
    service.sync_impact_actions(
        "impact-merge-b",
        advice=ActionAdviceOutput(
            category="experiment",
            title="做等预算方法对比（更新）",
            rationale="论文乙提出更强的可替代模块。",
            checklist=["共同步骤", "步骤乙一"],
        ),
    )

    experiment_actions = [
        item
        for item in service.list_actions(golden_case)
        if item.claim_revision_id == "claim-rev-06" and item.action_type == "experiment"
    ]
    assert len(experiment_actions) == 1
    merged = experiment_actions[0]
    # Highest severity wins; checklist merges without duplicates.
    assert merged.priority == "medium"
    assert merged.checklist_json == ["步骤甲一", "共同步骤", "步骤乙一"]
    assert merged.title == "做等预算方法对比（更新）"


def test_open_action_is_updated_across_scans_not_duplicated(
    db_session_factory, golden_case
):
    service = ActionService(db_session_factory)
    _make_impact(
        db_session_factory,
        "impact-rescan-a",
        "scan-demo-week-29",
        "claim-rev-06",
        stance="neutral",
        impact_mode="method_substitution",
    )
    first_ids = service.sync_impact_actions("impact-rescan-a")

    _make_scan(db_session_factory, "scan-rescan-2", golden_case)
    _make_impact(
        db_session_factory,
        "impact-rescan-b",
        "scan-rescan-2",
        "claim-rev-06",
        stance="neutral",
        impact_mode="method_substitution",
    )
    second_ids = service.sync_impact_actions("impact-rescan-b")

    assert first_ids == second_ids
    with db_session_factory() as session:
        action = session.get(ActionItem, first_ids[0])
        assert action.scan_run_id == "scan-rescan-2"
        assert action.impact_candidate_id == "impact-rescan-b"
        count = len(
            list(
                session.scalars(
                    select(ActionItem).where(
                        ActionItem.claim_revision_id == "claim-rev-06",
                        ActionItem.action_type == "experiment",
                    )
                )
            )
        )
    assert count == 1


def test_closed_action_reopens_as_fresh_row_on_later_scan(
    db_session_factory, golden_case
):
    service = ActionService(db_session_factory)
    _make_impact(
        db_session_factory,
        "impact-reopen-a",
        "scan-demo-week-29",
        "claim-rev-06",
        stance="neutral",
        impact_mode="method_substitution",
    )
    first_ids = service.sync_impact_actions("impact-reopen-a")
    service.update_status(first_ids[0], "done")

    _make_scan(db_session_factory, "scan-reopen-2", golden_case)
    _make_impact(
        db_session_factory,
        "impact-reopen-b",
        "scan-reopen-2",
        "claim-rev-06",
        stance="neutral",
        impact_mode="method_substitution",
    )
    second_ids = service.sync_impact_actions("impact-reopen-b")

    assert second_ids[0] != first_ids[0]
    with db_session_factory() as session:
        old = session.get(ActionItem, first_ids[0])
        new = session.get(ActionItem, second_ids[0])
        assert old.status == "done"
        assert new.status in {"proposed", "open"}
        assert new.scan_run_id == "scan-reopen-2"


def test_non_comparable_informative_impact_generates_cite_suggestion(
    db_session_factory, golden_case
):
    _make_impact(
        db_session_factory,
        "impact-test-incompatible",
        "scan-demo-week-29",
        "claim-rev-02",
        stance="neutral",
        impact_mode="prior_art",
        comparability="unknown",
        severity="informative",
        suggested_action="watch",
        review_state="informative",
    )
    service = ActionService(db_session_factory)
    action_ids = service.sync_impact_actions("impact-test-incompatible")

    with db_session_factory() as session:
        actions = [session.get(ActionItem, item_id) for item_id in action_ids]
    cite_actions = [item for item in actions if item.action_type == "cite"]
    assert len(cite_actions) == 1
    # Uncertainty-aware wording: not comparable must not read as a challenge.
    assert "不能直接支持或反驳" in cite_actions[0].rationale
    assert cite_actions[0].priority == "low"
