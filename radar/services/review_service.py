"""Human gate G1 for impact decisions."""

from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from radar.db import SessionLocal, session_scope
from radar.models import ActionItem, AuditEvent, ImpactCandidate, ReviewDecision, ScanRun


EDITABLE_FIELDS = {
    "stance", "impact_mode", "comparability", "change_depth", "severity", "suggested_action"
}


class ReviewService:
    def __init__(self, session_factory: sessionmaker[Session] = SessionLocal):
        self.session_factory = session_factory

    def _decide(
        self,
        impact_id: str,
        decision: str,
        edited_payload: dict | None = None,
        reason: str | None = None,
    ) -> ImpactCandidate:
        payload = dict(edited_payload or {})
        with session_scope(self.session_factory) as session:
            impact = session.get(ImpactCandidate, impact_id)
            if impact is None:
                raise LookupError(f"impact not found: {impact_id}")
            if impact.trust_state == "blocked" and decision != "dismiss":
                raise ValueError("state_blocked")
            previous_review_state = impact.review_state

            if decision == "edit":
                for field, value in payload.items():
                    if field in EDITABLE_FIELDS:
                        setattr(impact, field, value)
                impact.review_state = "edited"
            elif decision == "confirm":
                impact.review_state = "confirmed"
            elif decision == "dismiss":
                impact.review_state = "dismissed"
            else:
                raise ValueError(f"unsupported decision: {decision}")

            # Human review may change the impact mode or action, but a directional
            # scientific stance still requires programmatic condition compatibility.
            # The guard applies to confirm/edit only — dismissing an impact must
            # not rewrite its (irrelevant) stance — and every rewrite is recorded
            # in the decision payload instead of being silent.
            if decision in {"confirm", "edit"}:
                from radar.services.impact_service import ImpactService

                requested_stance = impact.stance
                impact.stance = ImpactService.enforce_stance(
                    impact.stance, impact.comparability
                )
                if impact.stance != requested_stance:
                    payload["condition_blocked"] = {
                        "requested_stance": requested_stance,
                        "enforced_stance": impact.stance,
                        "comparability": impact.comparability,
                    }

            actions = list(
                session.scalars(
                    select(ActionItem).where(
                        ActionItem.impact_candidate_id == impact.id
                    )
                )
            )
            for action in actions:
                if decision == "dismiss" and action.status != "done":
                    action.status = "dismissed"
                elif decision in {"confirm", "edit"} and (
                    action.status == "proposed"
                    or (
                        previous_review_state == "dismissed"
                        and action.status == "dismissed"
                    )
                ):
                    action.status = "open"

            session.add(
                ReviewDecision(
                    id=str(uuid4()),
                    impact_candidate_id=impact.id,
                    decision=decision,
                    edited_payload_json=payload,
                    reason=reason,
                )
            )
            scan = session.get(ScanRun, impact.scan_run_id)
            session.add(
                AuditEvent(
                    id=str(uuid4()),
                    case_id=scan.case_id if scan else "unknown",
                    event_type=f"impact_{decision}",
                    object_type="ImpactCandidate",
                    object_id=impact.id,
                    payload_json={"edited_payload": payload, "reason": reason},
                    actor_type="human",
                    actor_id="local_user",
                )
            )
            # Editing a previously informational comparison into prior-art/boundary
            # impact may create actions that did not exist when the scan
            # completed. Sync inside the decision transaction so a sync failure
            # rolls the decision back instead of leaving a committed decision
            # with unsynced actions.
            from radar.services.action_service import ActionService

            ActionService(self.session_factory).sync_impact_actions(
                impact_id, session=session
            )
            session.flush()
            session.expunge(impact)
            decided_impact = impact
        return decided_impact

    def confirm_impact(self, impact_id: str, reason: str | None = None) -> ImpactCandidate:
        return self._decide(impact_id, "confirm", reason=reason)

    def edit_impact(self, impact_id: str, payload: dict, reason: str | None = None) -> ImpactCandidate:
        return self._decide(impact_id, "edit", payload, reason)

    def dismiss_impact(self, impact_id: str, reason: str | None = None) -> ImpactCandidate:
        return self._decide(impact_id, "dismiss", reason=reason)

    def decisions_for_impact(self, impact_id: str) -> list[ReviewDecision]:
        with session_scope(self.session_factory) as session:
            return list(
                session.scalars(
                    select(ReviewDecision)
                    .where(ReviewDecision.impact_candidate_id == impact_id)
                    .order_by(ReviewDecision.created_at)
                )
            )
