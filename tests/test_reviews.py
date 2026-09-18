import pytest
from sqlalchemy import func, select

from radar.models import (
    ActionItem,
    Claim,
    ClaimRevision,
    ClaimSourceLink,
    ImpactCandidate,
    ManuscriptVersion,
    ResearchCase,
    ReviewDecision,
    Source,
    SourceSnapshot,
)
from radar.services.action_service import ActionService
from radar.services.impact_service import ImpactService
from radar.services.ledger_service import LedgerService
from radar.services.review_service import ReviewService


def test_candidate_isolation_and_persistent_review(db_session_factory, golden_case):
    ledger = LedgerService(db_session_factory)
    assert ledger.get_claim_health("claim-01") == "active"
    assert ledger.get_claim_ledger("claim-01")["challenges"] == []

    ReviewService(db_session_factory).confirm_impact("impact-01")
    assert LedgerService(db_session_factory).get_claim_health("claim-01") == "contested"
    assert len(LedgerService(db_session_factory).get_claim_ledger("claim-01")["challenges"]) == 1


def test_stance_rewrite_is_recorded_not_silent(db_session_factory, golden_case):
    """M9 regression: a human edit requesting a directional stance under
    incompatible conditions is rewritten to uncertain AND the rewrite is
    recorded in the decision payload — never silent."""
    with db_session_factory() as session:
        impact = session.get(ImpactCandidate, "impact-01")
        impact.comparability = "incompatible"
        impact.review_state = "candidate"
        session.commit()

    service = ReviewService(db_session_factory)
    decided = service.edit_impact(
        "impact-01",
        {"stance": "challenges", "comparability": "incompatible"},
    )

    assert decided.stance == "uncertain"  # invariant holds
    decisions = service.decisions_for_impact("impact-01")
    latest = decisions[-1]
    assert latest.decision == "edit"
    blocked = latest.edited_payload_json.get("condition_blocked")
    assert blocked is not None
    assert blocked["requested_stance"] == "challenges"
    assert blocked["enforced_stance"] == "uncertain"
    assert blocked["comparability"] == "incompatible"


def test_dismiss_does_not_rewrite_stance(db_session_factory, golden_case):
    """M9 regression: dismissing an impact must not mutate its stance."""
    with db_session_factory() as session:
        impact = session.get(ImpactCandidate, "impact-01")
        impact.comparability = "incompatible"
        impact.stance = "challenges"
        impact.review_state = "candidate"
        session.commit()

    decided = ReviewService(db_session_factory).dismiss_impact("impact-01")

    assert decided.stance == "challenges"  # untouched
    assert decided.review_state == "dismissed"


def test_retraction_requires_confirmation_for_health_change(db_session_factory, golden_case):
    ledger = LedgerService(db_session_factory)
    assert ledger.get_claim_health("claim-09") == "active"
    assert ImpactService(db_session_factory).propagate_retraction("source-07") == ["impact-07"]
    assert ledger.get_claim_health("claim-09") == "active"
    ReviewService(db_session_factory).confirm_impact("impact-07")
    assert ledger.get_claim_health("claim-09") == "revalidation_required"


def test_dismissed_impact_never_enters_ledger(db_session_factory, golden_case):
    ReviewService(db_session_factory).dismiss_impact("impact-02")
    assert LedgerService(db_session_factory).get_claim_ledger("claim-02")["supports"] == []


def test_re_adopting_a_dismissed_impact_reopens_its_actions(
    db_session_factory, golden_case
):
    service = ReviewService(db_session_factory)
    service.dismiss_impact("impact-03")
    service.confirm_impact("impact-03")

    with db_session_factory() as session:
        impact = session.get(ImpactCandidate, "impact-03")
        statuses = set(
            session.scalars(
                select(ActionItem.status).where(
                    ActionItem.impact_candidate_id == "impact-03"
                )
            )
        )
    assert impact.review_state == "confirmed"
    assert statuses == {"open"}


def test_propagate_retraction_without_scan_run_is_skipped(db_session_factory):
    """A case with no ScanRun must not crash propagate_retraction on scan.id."""
    with db_session_factory() as session:
        session.add(
            ResearchCase(
                id="case-no-scan", title="No-scan case", research_question="q"
            )
        )
        session.flush()
        session.add(
            ManuscriptVersion(
                id="manuscript-no-scan",
                case_id="case-no-scan",
                version_no=1,
                file_name="main.tex",
                source_type="latex",
                content_text="body",
                content_hash="hash-no-scan",
                is_current=True,
            )
        )
        session.add(Claim(id="claim-no-scan", case_id="case-no-scan", stable_key="C1"))
        session.flush()
        session.add(
            ClaimRevision(
                id="claim-rev-no-scan",
                claim_id="claim-no-scan",
                manuscript_version_id="manuscript-no-scan",
                statement="s",
                centrality="major",
                contract_json={},
                falsifiable_condition="f",
                source_quote="q",
                source_locator="loc:1",
                review_state="confirmed",
            )
        )
        session.add(
            Source(
                id="source-no-scan",
                external_id="ext:no-scan",
                title="t",
                url="https://example.org/no-scan",
                integrity_state="retracted",
            )
        )
        session.add(
            SourceSnapshot(
                id="snapshot-no-scan",
                source_id="source-no-scan",
                version_label="v1",
                title="t",
                abstract="a",
                content_text="c",
                content_hash="hash-snap-no-scan",
            )
        )
        session.flush()
        session.add(
            ClaimSourceLink(
                id="link-no-scan",
                claim_revision_id="claim-rev-no-scan",
                source_id="source-no-scan",
                relation_type="cited_by_claim",
                source_locator="loc:1",
                review_state="confirmed",
            )
        )
        session.commit()

    assert ImpactService(db_session_factory).propagate_retraction("source-no-scan") == []
    with db_session_factory() as session:
        assert session.scalar(select(func.count(ImpactCandidate.id))) == 0


def test_decision_rolls_back_when_action_sync_fails(
    db_session_factory, golden_case, monkeypatch
):
    def failing_sync(self, impact_id, session=None):
        raise RuntimeError("injected_sync_failure")

    monkeypatch.setattr(ActionService, "sync_impact_actions", failing_sync)
    with pytest.raises(RuntimeError, match="injected_sync_failure"):
        ReviewService(db_session_factory).confirm_impact("impact-02")

    with db_session_factory() as session:
        impact = session.get(ImpactCandidate, "impact-02")
        decisions = session.scalar(
            select(func.count(ReviewDecision.id)).where(
                ReviewDecision.impact_candidate_id == "impact-02"
            )
        )
    assert impact.review_state == "candidate"
    assert decisions == 0
