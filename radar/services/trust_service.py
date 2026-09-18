"""Hard trust invariants for claims, impacts, and patches."""

from radar.schemas import ImpactAssessmentOutput, TrustResult
from radar.services.evidence_service import EvidenceService


class TrustService:
    def verify_claim_quote(self, quote: str, manuscript_content: str) -> TrustResult:
        if not quote or quote not in manuscript_content:
            return TrustResult(state="blocked", errors=["span_failed"])
        return TrustResult(state="verified", errors=[])

    def verify_impact(
        self,
        impact: ImpactAssessmentOutput,
        manuscript_content: str,
        incoming_content: str,
    ) -> TrustResult:
        errors: list[str] = []
        if not EvidenceService.verify_exact(impact.evidence_own, manuscript_content):
            errors.append("span_failed:own")
        if not EvidenceService.verify_exact(impact.evidence_new, incoming_content):
            errors.append("span_failed:new")
        if (
            impact.stance in {"supports", "challenges"}
            and impact.comparability != "compatible"
        ):
            errors.append("condition_not_compatible_for_directional_stance")
        if errors:
            return TrustResult(state="blocked", errors=errors)
        return TrustResult(state="verified", errors=[])
