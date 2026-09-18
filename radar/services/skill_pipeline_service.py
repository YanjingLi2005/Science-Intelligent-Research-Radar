"""CCF-A skill pipeline: run a sequence of skills in one request.

A researcher often wants a full revision loop: write suggested edits, audit
their integrity, then review submission readiness. This service executes the
registered CCF-A skills in order and returns each result together.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from sqlalchemy.orm import Session, sessionmaker

from radar.db import SessionLocal, session_scope
from radar.models import AuditEvent
from radar.services.skill_execution_service import SkillExecutionService

PIPELINES: dict[str, list[str]] = {
    "revision": [
        "ccf_paper_writer",
        "ccf_integrity_auditor",
        "ccf_paper_reviewer",
    ],
    "review": [
        "ccf_paper_reviewer",
        "ccf_reference_auditor",
    ],
    "experiment": [
        "ccf_experiment_designer",
        "ccf_integrity_auditor",
    ],
}

SKILL_ACTION: dict[str, str] = {
    "ccf_paper_writer": "writing",
    "ccf_integrity_auditor": "audit",
    "ccf_paper_reviewer": "review",
    "ccf_experiment_designer": "experiment",
    "ccf_rebuttal_writer": "rebuttal",
    "ccf_reference_auditor": "audit_references",
}


class SkillPipelineService:
    def __init__(self, session_factory: sessionmaker[Session] = SessionLocal):
        self.session_factory = session_factory

    def run(
        self,
        case_id: str,
        *,
        pipeline: str = "revision",
        skill_names: list[str] | None = None,
        impact_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        names = skill_names or PIPELINES.get(pipeline)
        if not names:
            raise ValueError(f"unknown skill pipeline: {pipeline}")
        executor = SkillExecutionService(self.session_factory)
        results = []
        for skill_name in names:
            action_type = SKILL_ACTION.get(skill_name)
            if not action_type:
                raise ValueError(f"no action mapping for skill: {skill_name}")
            results.append(
                executor.execute(
                    case_id,
                    skill_name=skill_name,
                    action_type=action_type,
                    impact_ids=impact_ids or [],
                )
            )
        if self.session_factory is not None:
            with session_scope(self.session_factory) as session:
                session.add(
                    AuditEvent(
                        id=str(uuid4()),
                        case_id=case_id,
                        event_type="skill_pipeline_executed",
                        object_type="SkillPipeline",
                        object_id=case_id,
                        payload_json={
                            "pipeline": pipeline if not skill_names else "custom",
                            "skills": names,
                        },
                        actor_type="model",
                        actor_id="skill_pipeline",
                    )
                )
        return {
            "pipeline": pipeline if not skill_names else "custom",
            "skills": names,
            "results": results,
        }
