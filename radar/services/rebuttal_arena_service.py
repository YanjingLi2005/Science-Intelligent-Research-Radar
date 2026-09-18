"""Reviewer persona simulation and rebuttal generation."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from radar.db import SessionLocal, session_scope
from radar.llm.text_utils import truncate_for_prompt
from radar.models import Claim, ClaimRevision, ImpactCandidate, ManuscriptVersion, ResearchCase
from radar.skills.reviewer_personas import REVIEWER_PERSONAS, REVIEWER_PERSONAS_BY_ID
from radar.skills.registry import get_registry


def _empty_review(warning: str) -> dict[str, Any]:
    return {"overall_score": None, "dimensions": [], "risks": [], "action_priorities": [], "warnings": [warning]}


def _empty_rebuttal(warning: str) -> dict[str, Any]:
    return {"rebuttal_points": [], "revision_plan": [], "strength_assessment": "weak", "warnings": [warning]}


class RebuttalArenaService:
    def __init__(self, session_factory: sessionmaker[Session] = SessionLocal):
        self.session_factory = session_factory

    def _load_project_state(self, case_id: str) -> dict[str, Any]:
        with session_scope(self.session_factory) as session:
            case = session.get(ResearchCase, case_id)
            if case is None:
                raise LookupError(f"case not found: {case_id}")

            claim_rows = list(
                session.execute(
                    select(Claim, ClaimRevision)
                    .join(ClaimRevision, ClaimRevision.claim_id == Claim.id)
                    .where(Claim.case_id == case_id, ClaimRevision.review_state == "confirmed")
                )
            )
            manuscript = session.scalar(
                select(ManuscriptVersion).where(
                    ManuscriptVersion.case_id == case_id,
                    ManuscriptVersion.is_current.is_(True),
                )
            )
            impacts = list(
                session.scalars(
                    select(ImpactCandidate)
                    .join(ClaimRevision, ClaimRevision.id == ImpactCandidate.claim_revision_id)
                    .join(Claim, Claim.id == ClaimRevision.claim_id)
                    .where(
                        Claim.case_id == case_id,
                        ImpactCandidate.review_state.in_(("confirmed", "edited")),
                    )
                )
            )
            return {
                "case_id": case_id,
                "title": case.title,
                "research_question": case.research_question,
                "claims": [
                    {
                        "id": revision.id,
                        "stable_key": claim.stable_key,
                        "statement": revision.statement,
                        "centrality": revision.centrality,
                        "contract": revision.contract_json,
                    }
                    for claim, revision in claim_rows
                ],
                "manuscript_summary": {
                    "content": truncate_for_prompt(manuscript.content_text, max_chars=50_000, purpose="arena project state")
                    if manuscript else "",
                    "source_type": manuscript.source_type if manuscript else "",
                },
                "impacts": [
                    {
                        "stance": impact.stance,
                        "impact_mode": impact.impact_mode,
                        "comparability": impact.comparability,
                        "condition_differences": impact.condition_differences_json,
                        "suggested_action": impact.suggested_action,
                        "severity": impact.severity,
                    }
                    for impact in impacts
                ],
            }

    @staticmethod
    def _concerns(review: dict[str, Any]) -> list[dict[str, Any]]:
        concerns = []
        concerns.extend(
            {"type": "dimension", "name": item.get("name", ""), "score": item.get("score"), "comment": item.get("comment", "")}
            for item in review.get("dimensions", [])
        )
        concerns.extend(
            {"type": "risk", **risk} for risk in review.get("risks", [])
        )
        return concerns

    def simulate(self, case_id: str, personas: list[str] | None, focus: str = "") -> dict[str, Any]:
        project_state = self._load_project_state(case_id)
        selected_ids = personas or [persona["id"] for persona in REVIEWER_PERSONAS]
        unknown = [persona_id for persona_id in selected_ids if persona_id not in REVIEWER_PERSONAS_BY_ID]
        if unknown:
            raise ValueError(f"unknown reviewer persona: {', '.join(unknown)}")

        registry = get_registry()
        results = []
        warnings: list[str] = []
        for persona_id in selected_ids:
            persona = REVIEWER_PERSONAS_BY_ID[persona_id]
            review = _empty_review("llm_unavailable")
            rebuttal = _empty_rebuttal("llm_unavailable")
            try:
                review_output = registry.execute(
                    "ccf_paper_reviewer",
                    {
                        "action_type": "review",
                        "focus": f"Persona: {persona['label']} — {persona['description']}\n{focus}".strip(),
                        "confirmed_impacts": project_state["impacts"],
                        "registry": registry,
                    },
                    project_state=project_state,
                )
                review = dict(review_output.content)
                review.setdefault("warnings", review_output.warnings)
                concerns = self._concerns(review)
                rebuttal_output = registry.execute(
                    "ccf_rebuttal_writer",
                    {
                        "action_type": "rebuttal",
                        "confirmed_impacts": concerns,
                        "completed_actions": review.get("action_priorities", []),
                        "registry": registry,
                    },
                    project_state=project_state,
                )
                rebuttal = dict(rebuttal_output.content)
                rebuttal.setdefault("warnings", rebuttal_output.warnings)
            except Exception as exc:
                warning = f"persona_{persona_id}_failed: {exc}"
                warnings.append(warning)
                review = _empty_review(warning)
                rebuttal = _empty_rebuttal(warning)
            results.append({"id": persona["id"], "label": persona["label"], "description": persona["description"], "review": review, "rebuttal": rebuttal})

        scores = [item["review"].get("overall_score") for item in results if item["review"].get("overall_score") is not None]
        return {
            "case_id": case_id,
            "personas": results,
            "overall": {
                "persona_count": len(results),
                "average_score": round(sum(scores) / len(scores), 2) if scores else None,
                "warnings": warnings,
            },
        }
