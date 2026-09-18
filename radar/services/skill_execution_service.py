"""Server-side CCF-A skill execution, shared by the HTTP API and MCP server."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from radar.db import SessionLocal, session_scope
from radar.llm.text_utils import truncate_for_prompt
from radar.models import (
    Claim,
    ClaimRevision,
    ImpactCandidate,
    ManuscriptVersion,
    ModelRun,
    ResearchCase,
    SourceSnapshot,
)
from radar.services.evidence_service import EvidenceService


class SkillExecutionService:
    def __init__(self, session_factory: sessionmaker[Session] = SessionLocal):
        self.session_factory = session_factory

    def execute(
        self,
        case_id: str,
        *,
        skill_name: str = "",
        action_type: str,
        impact_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        from radar.skills.registry import get_registry

        registry = get_registry()
        impact_ids = impact_ids or []

        with session_scope(self.session_factory) as session:
            research_case = session.get(ResearchCase, case_id)
            if not research_case:
                raise LookupError(f"case not found: {case_id}")

            claim_rows = list(
                session.execute(
                    select(Claim, ClaimRevision)
                    .join(ClaimRevision, ClaimRevision.claim_id == Claim.id)
                    .where(
                        Claim.case_id == case_id,
                        ClaimRevision.review_state == "confirmed",
                    )
                )
            )
            claims = [revision for _, revision in claim_rows]
            claim_by_id = {
                claim.id: {"stable_key": claim.stable_key}
                for claim, _ in claim_rows
            }

            manuscript = session.scalar(
                select(ManuscriptVersion).where(
                    ManuscriptVersion.case_id == case_id,
                    ManuscriptVersion.is_current.is_(True),
                )
            )

            impacts = []
            for iid in impact_ids:
                imp = session.get(ImpactCandidate, iid)
                if imp:
                    snapshot = session.get(SourceSnapshot, imp.source_snapshot_id)
                    # Skills draft real manuscript text, so they need what the
                    # incoming paper actually reported — not only a stance label.
                    # Findings live on the incoming_result ModelRun; each quote is
                    # re-verified against the paper before it can be cited.
                    findings: list[dict] = []
                    if snapshot is not None:
                        try:
                            runs = list(
                                session.scalars(
                                    select(ModelRun).where(
                                        ModelRun.stage == "incoming_result"
                                    )
                                )
                            )
                            run = next(
                                (
                                    item
                                    for item in runs
                                    if snapshot.id in (item.input_refs_json or [])
                                ),
                                None,
                            )
                            if run is not None:
                                findings = [
                                    item
                                    for item in (run.parsed_output_json or {}).get(
                                        "reported_findings", []
                                    )
                                    if EvidenceService.resolve_exact_quote_present(
                                        item.get("quote", ""), snapshot.content_text
                                    )
                                ]
                        except Exception:
                            findings = []
                    impacts.append(
                        {
                            "id": imp.id,
                            "stance": imp.stance,
                            "impact_mode": imp.impact_mode,
                            "comparability": imp.comparability,
                            "suggested_action": imp.suggested_action,
                            "condition_differences": imp.condition_differences_json,
                            "incoming_evidence_quote": (
                                (imp.evidence_new_json or {}).get("quote", "")
                            ),
                            "reported_findings": findings,
                        }
                    )

        project_state = {
            "title": research_case.title,
            "research_question": research_case.research_question,
            "case_id": case_id,
            "claims": [
                {
                    "id": c.id,
                    "stable_key": claim_by_id.get(c.claim_id, {}).get("stable_key", ""),
                    "statement": c.statement,
                    "centrality": c.centrality,
                    "contract": c.contract_json,
                }
                for c in claims
            ],
            "manuscript": (
                {
                    "content": truncate_for_prompt(
                        manuscript.content_text,
                        max_chars=50_000,
                        purpose="skill project state",
                    ),
                    "source_type": manuscript.source_type,
                }
                if manuscript
                else {}
            ),
        }

        request = {
            "action_type": action_type,
            "confirmed_impacts": impacts,
            "registry": registry,
        }

        if not skill_name:
            skill_name = registry.route(request) or ""
            if not skill_name:
                raise LookupError(f"no skill handles action type: {action_type}")
        elif skill_name not in registry.names():
            raise LookupError(f"unknown skill: {skill_name}")
        elif not registry.can_handle(skill_name, request):
            raise ValueError(
                f"skill {skill_name} cannot handle action type: {action_type}"
            )

        output = registry.execute(skill_name, request, project_state=project_state)
        return {
            "skill": skill_name,
            "artifact_type": output.artifact_type,
            "content": output.content,
            "warnings": output.warnings,
            "handoff_suggestions": output.handoff_suggestions,
        }
