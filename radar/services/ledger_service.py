"""Confirmed-only claim ledger and derived health."""

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from radar.db import SessionLocal, session_scope
from radar.models import Claim, ClaimRevision, ImpactCandidate, Source, SourceSnapshot


CONFIRMED_STATES = {"confirmed", "edited"}


class LedgerService:
    def __init__(self, session_factory: sessionmaker[Session] = SessionLocal):
        self.session_factory = session_factory

    def current_revision(self, claim_id: str) -> ClaimRevision | None:
        with session_scope(self.session_factory) as session:
            revision = session.scalar(
                select(ClaimRevision)
                .where(
                    ClaimRevision.claim_id == claim_id,
                    ClaimRevision.review_state == "confirmed",
                )
                .order_by(ClaimRevision.revision_no.desc())
            )
            if revision:
                session.expunge(revision)
            return revision

    def get_claim_health(self, claim_id: str) -> str:
        with session_scope(self.session_factory) as session:
            revision_ids = list(
                session.scalars(select(ClaimRevision.id).where(ClaimRevision.claim_id == claim_id))
            )
            impacts = list(
                session.scalars(
                    select(ImpactCandidate).where(
                        ImpactCandidate.claim_revision_id.in_(revision_ids),
                        ImpactCandidate.review_state.in_(CONFIRMED_STATES),
                    )
                )
            ) if revision_ids else []
            if any(item.impact_mode == "research_integrity" for item in impacts):
                return "revalidation_required"
            if any(item.stance == "challenges" for item in impacts):
                return "contested"
            if any(item.stance == "supports" for item in impacts):
                return "corroborated"
            return "active"

    def get_claim_ledger(self, claim_id: str) -> dict:
        with session_scope(self.session_factory) as session:
            claim = session.get(Claim, claim_id)
            if claim is None:
                raise LookupError(f"claim not found: {claim_id}")
            revision = session.scalar(
                select(ClaimRevision)
                .where(ClaimRevision.claim_id == claim_id, ClaimRevision.review_state == "confirmed")
                .order_by(ClaimRevision.revision_no.desc())
            )
            revision_ids = list(
                session.scalars(select(ClaimRevision.id).where(ClaimRevision.claim_id == claim_id))
            )
            impacts = list(
                session.scalars(
                    select(ImpactCandidate).where(
                        ImpactCandidate.claim_revision_id.in_(revision_ids),
                        ImpactCandidate.review_state.in_(CONFIRMED_STATES),
                    ).order_by(ImpactCandidate.created_at.desc())
                )
            ) if revision_ids else []
            entries = []
            for impact in impacts:
                snapshot = session.get(SourceSnapshot, impact.source_snapshot_id)
                source = session.get(Source, snapshot.source_id) if snapshot else None
                entries.append(
                    {
                        "id": impact.id,
                        "stance": impact.stance,
                        "impact_mode": impact.impact_mode,
                        "severity": impact.severity,
                        "action": impact.suggested_action,
                        "comparability": impact.comparability,
                        "condition_differences": impact.condition_differences_json,
                        "source_title": source.title if source else "Unknown source",
                        "source_url": source.url if source else "",
                        "evidence": impact.evidence_new_json,
                        "review_state": impact.review_state,
                    }
                )
            return {
                "claim_id": claim.id,
                "stable_key": claim.stable_key,
                "statement": revision.statement if revision else "",
                "centrality": revision.centrality if revision else "",
                "contract": revision.contract_json if revision else {},
                "health": self.get_claim_health(claim_id),
                "supports": [entry for entry in entries if entry["stance"] == "supports"],
                "challenges": [entry for entry in entries if entry["stance"] == "challenges"],
                "integrity": [entry for entry in entries if entry["impact_mode"] == "research_integrity"],
                "confirmed_decisions": entries,
            }
