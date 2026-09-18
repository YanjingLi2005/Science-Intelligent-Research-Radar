"""Agent skill execution, pipeline workflows, rebuttal arena simulation, and audit logs."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Form, HTTPException
from sqlalchemy import select

from radar.api_schemas import (
    ArenaSimulationRequest,
    AuditRecOut,
    SkillPipelineRequest,
)
from radar.db import session_scope
from radar.models import AuditEvent, ModelRun
from radar.services.rebuttal_arena_service import RebuttalArenaService
from radar.services.skill_execution_service import SkillExecutionService
from radar.services.skill_pipeline_service import SkillPipelineService
from radar.routes.builders import _build_audit
from radar.routes.deps import _parse_json_field, _service

router = APIRouter(tags=["skills"])


@router.post("/api/cases/{case_id}/skills/execute")
def execute_skill(case_id: str, skill_name: str = Form(""), action_type: str = Form(...), impact_ids: str = Form("[]")) -> dict[str, Any]:
    """Execute a CCF-A style skill on a research case."""
    try:
        return _service(SkillExecutionService).execute(
            case_id,
            skill_name=skill_name,
            action_type=action_type,
            impact_ids=_parse_json_field(impact_ids, []),
        )
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@router.post("/api/cases/{case_id}/skill-pipeline")
def run_skill_pipeline(case_id: str, body: SkillPipelineRequest) -> dict[str, Any]:
    """Run a sequence of CCF-A skills in one request."""
    try:
        return _service(SkillPipelineService).run(
            case_id,
            pipeline=body.pipeline,
            skill_names=body.skill_names,
            impact_ids=body.impact_ids,
        )
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@router.post("/api/cases/{case_id}/arena/simulate")
def simulate_rebuttal_arena(case_id: str, body: ArenaSimulationRequest) -> dict[str, Any]:
    """Simulate selected reviewer personas and draft rebuttal responses."""
    try:
        return _service(RebuttalArenaService).simulate(case_id, body.personas, body.focus)
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@router.get("/api/cases/{case_id}/audit", response_model=list[AuditRecOut])
def export_audit(case_id: str) -> list[dict[str, Any]]:
    """Export the audit trail (model runs + audit events) as a list."""
    return _build_audit(case_id)


@router.get("/api/cases/{case_id}/skill-runs")
def list_skill_runs(case_id: str) -> list[dict[str, Any]]:
    """Return recent CCF-A skill execution records for a case."""
    with session_scope() as session:
        runs = list(
            session.scalars(
                select(ModelRun)
                .where(
                    ModelRun.case_id == case_id,
                    ModelRun.stage.like("skill_%"),
                )
                .order_by(ModelRun.created_at.desc())
                .limit(50)
            )
        )
        return [
            {
                "id": run.id,
                "stage": run.stage,
                "provider": run.provider,
                "model": run.model,
                "created_at": run.created_at.isoformat() if run.created_at else None,
                "latency_ms": run.latency_ms,
                "input_tokens": run.input_tokens,
                "output_tokens": run.output_tokens,
                "output": run.parsed_output_json,
            }
            for run in runs
        ]


@router.get("/api/cases/{case_id}/skill-pipeline-runs")
def list_skill_pipeline_runs(case_id: str) -> list[dict[str, Any]]:
    """Return recent CCF-A skill pipeline execution records."""
    with session_scope() as session:
        events = list(
            session.scalars(
                select(AuditEvent)
                .where(
                    AuditEvent.case_id == case_id,
                    AuditEvent.event_type == "skill_pipeline_executed",
                )
                .order_by(AuditEvent.created_at.desc())
                .limit(50)
            )
        )
        return [
            {
                "id": event.id,
                "event_type": event.event_type,
                "created_at": event.created_at.isoformat() if event.created_at else None,
                "payload": event.payload_json,
            }
            for event in events
        ]


@router.get("/api/cases/{case_id}/notification-history")
def list_notification_history(
    case_id: str, event_type: str | None = None
) -> list[dict[str, Any]]:
    """Return recent scheduler notification events (webhook sent/failed, auto-scan failures)."""
    allowed_types = [
        "cost_alert_webhook_sent",
        "cost_alert_webhook_failed",
        "auto_scan_start_failed",
    ]
    with session_scope() as session:
        query = select(AuditEvent).where(AuditEvent.case_id == case_id)
        if event_type:
            query = query.where(AuditEvent.event_type == event_type)
        else:
            query = query.where(AuditEvent.event_type.in_(allowed_types))
        events = list(
            session.scalars(
                query.order_by(AuditEvent.created_at.desc()).limit(50)
            )
        )
        return [
            {
                "id": event.id,
                "event_type": event.event_type,
                "created_at": event.created_at.isoformat() if event.created_at else None,
                "payload": event.payload_json,
            }
            for event in events
        ]
