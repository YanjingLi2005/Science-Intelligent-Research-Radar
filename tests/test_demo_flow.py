from sqlalchemy import func, select

from radar.models import AuditEvent
from radar.services.ledger_service import LedgerService
from radar.services.patch_service import PatchService
from radar.services.report_service import ReportService
from radar.services.review_service import ReviewService


def test_complete_demo_flow(db_session_factory, golden_case):
    summary = ReportService(db_session_factory).get_weekly_summary("scan-demo-week-29")
    assert summary["scanned_papers"] == 20
    assert summary["related_papers"] == 7
    assert summary["competitor_alerts"] == 1
    assert summary["integrity_alerts"] == 1
    action_report = ReportService(db_session_factory).get_weekly_action_report(
        "scan-demo-week-29"
    )
    assert action_report["open_actions"] >= 8
    assert action_report["urgent"] >= 3

    ReviewService(db_session_factory).edit_impact(
        "impact-01", {"suggested_action": "add_boundary_discussion"}
    )
    ledger = LedgerService(db_session_factory).get_claim_ledger("claim-01")
    assert ledger["health"] == "contested"
    patch = PatchService(db_session_factory).generate_patch("impact-01")
    PatchService(db_session_factory).approve_patch(patch.id)
    pack = ReportService(db_session_factory).get_evidence_pack("claim-01")
    assert len(pack["challenges"]) == 1
    with db_session_factory() as session:
        assert session.scalar(select(func.count(AuditEvent.id))) >= 4
