"""MCP server for Research Radar.

Exposes the local product as Model Context Protocol tools so Claude, Cursor,
Codex, DSH agents and other MCP clients can trigger scans, read impacts,
run deep research, and ask questions over the collected literature.

Run with:

    python -m radar.mcp_server

or point your MCP client at this module (stdio transport).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from mcp.server.fastmcp import FastMCP
from sqlalchemy import select

from radar.db import SessionLocal, session_scope
from radar.models import AuditEvent, ModelRun, ResearchCase, ScanRun
from radar.services.action_service import ActionService
from radar.services.case_service import CaseService
from radar.services.digest_service import DigestService
from radar.services.literature_qa_service import LiteratureQAService
from radar.services.scan_runner import start, start_deep_research
from radar.services.scheduler_service import (
    get_auto_scan,
    set_auto_scan,
    get_digest_webhook,
    set_digest_webhook,
)
from radar.services.skill_execution_service import SkillExecutionService
from radar.services.skill_pipeline_service import SkillPipelineService
from radar.services.source_import_service import SourceImportService

mcp = FastMCP("Research Radar")

# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@mcp.tool()
def list_cases() -> list[dict[str, Any]]:
    """List all research cases (monitoring objects)."""
    cases = CaseService(SessionLocal).list_cases()
    return [
        {
            "id": case.id,
            "title": case.title,
            "research_question": case.research_question,
            "field": case.field,
            "updated_at": case.updated_at.isoformat() if case.updated_at else None,
        }
        for case in cases
    ]


@mcp.tool()
def get_case(case_id: str) -> dict[str, Any]:
    """Get one research case's metadata."""
    with session_scope(SessionLocal) as session:
        case = session.get(ResearchCase, case_id)
        if case is None:
            return {"error": f"case not found: {case_id}"}
        return {
            "id": case.id,
            "title": case.title,
            "research_question": case.research_question,
            "field": case.field,
            "settings_json": case.settings_json or {},
            "updated_at": case.updated_at.isoformat() if case.updated_at else None,
        }


@mcp.tool()
def list_sources(case_id: str) -> list[dict[str, Any]]:
    """List case-referenced literature sources and their snapshot counts."""
    from radar.api import list_sources as api_list_sources

    return api_list_sources(case_id)


@mcp.tool()
def cost_by_source(case_id: str) -> dict[str, Any]:
    """Return LLM cost grouped by source kind for a case."""
    from radar.api import cost_by_source as api_cost_by_source

    return api_cost_by_source(case_id)


@mcp.tool()
def remove_sources(case_id: str, source_ids: list[str]) -> dict[str, Any]:
    """Remove source references from a case without deleting global sources."""
    from radar.api import remove_sources as api_remove_sources
    from radar.api_schemas import SourceRemoveRequest

    return api_remove_sources(case_id, SourceRemoveRequest(source_ids=source_ids))


@mcp.tool()
def get_audit(case_id: str) -> list[dict[str, Any]]:
    """Return the audit trail for a case."""
    from radar.api import export_audit

    return export_audit(case_id)


@mcp.tool()
def get_retrieval_receipts(case_id: str) -> list[dict[str, Any]]:
    """Return retrieval receipts for a case."""
    from radar.api import list_retrieval_receipts

    return list_retrieval_receipts(case_id)


@mcp.tool()
def get_citation_health(case_id: str) -> dict[str, Any]:
    """Return citation health for a case."""
    from radar.api import get_citation_health

    return get_citation_health(case_id)


@mcp.tool()
def get_source_detail(case_id: str, source_id: str) -> dict[str, Any]:
    """Get one case-referenced source and its related evidence."""
    from radar.api import get_source as api_get_source

    return api_get_source(case_id, source_id)


@mcp.tool()
def list_scans(case_id: str) -> list[dict[str, Any]]:
    """List scan/deep-research runs for a case (most recent first)."""
    with session_scope(SessionLocal) as session:
        scans = list(
            session.scalars(
                select(ScanRun)
                .where(ScanRun.case_id == case_id)
                .order_by(ScanRun.created_at.desc())
            )
        )
        return [
            {
                "id": scan.id,
                "mode": scan.mode,
                "status": scan.status,
                "started_at": scan.started_at.isoformat() if scan.started_at else None,
                "finished_at": scan.finished_at.isoformat() if scan.finished_at else None,
                "stats": scan.stats_json or {},
                "error_message": scan.error_message,
            }
            for scan in scans
        ]


@mcp.tool()
def get_scan_status(case_id: str, scan_id: str) -> dict[str, Any]:
    """Get one scan/deep-research run's status and stats."""
    with session_scope(SessionLocal) as session:
        scan = session.get(ScanRun, scan_id)
        if scan is None:
            return {"error": f"scan not found: {scan_id}"}
        return {
            "id": scan.id,
            "case_id": scan.case_id,
            "mode": scan.mode,
            "status": scan.status,
            "started_at": scan.started_at.isoformat() if scan.started_at else None,
            "finished_at": scan.finished_at.isoformat() if scan.finished_at else None,
            "progress": (scan.stats_json or {}).get("progress", {}),
            "stats": {k: v for k, v in (scan.stats_json or {}).items() if k != "progress"},
            "error_message": scan.error_message,
        }


@mcp.tool()
def get_monitoring_stats(case_id: str) -> dict[str, Any]:
    """Return monitoring health stats: scan counts, failures, last scan."""
    with session_scope(SessionLocal) as session:
        scans = list(
            session.scalars(select(ScanRun).where(ScanRun.case_id == case_id))
        )
        status_counts: dict[str, int] = {}
        for scan in scans:
            status_counts[scan.status] = status_counts.get(scan.status, 0) + 1
        auto_failures = len(
            list(
                session.scalars(
                    select(AuditEvent).where(
                        AuditEvent.case_id == case_id,
                        AuditEvent.event_type == "auto_scan_start_failed",
                    )
                )
            )
        )
        last_scan_at = max(
            (scan.started_at for scan in scans if scan.started_at),
            default=None,
        )
        from datetime import datetime, timedelta, timezone
        from sqlalchemy import func

        cutoff = datetime.now(timezone.utc) - timedelta(days=30)
        monthly_cost = float(
            session.scalar(
                select(func.sum(ModelRun.estimated_cost)).where(
                    ModelRun.case_id == case_id,
                    ModelRun.created_at >= cutoff,
                )
            )
            or 0.0
        )
        case = session.get(ResearchCase, case_id)
        cost_alert = (case.settings_json or {}).get("cost_alert") if case else {}
        cost_alert_enabled = bool(cost_alert.get("enabled", False))
        budget = float(cost_alert.get("monthly_budget_usd", 0.0))
        return {
            "total_scans": len(scans),
            "status_counts": status_counts,
            "auto_scan_start_failures": auto_failures,
            "last_scan_at": last_scan_at.isoformat() if last_scan_at else None,
            "monthly_cost_usd": round(monthly_cost, 4),
            "cost_alert_exceeded": cost_alert_enabled and budget > 0 and monthly_cost > budget,
        }


@mcp.tool()
def start_scan(case_id: str, max_results: int = 32, analysis_limit: int = 3) -> dict[str, Any]:
    """Start a claim-driven literature radar scan for a case."""
    scan_id = start(
        case_id,
        max_results=max_results,
        analysis_limit=analysis_limit,
    )
    return {"scan_id": scan_id, "status": "running", "mode": "auto_public_paper_radar"}


@mcp.tool()
def start_deep_research_tool(
    case_id: str,
    question: str = "",
    depth: int = 1,
    max_sources: int = 8,
    max_subqueries: int = 3,
) -> dict[str, Any]:
    """Start a bounded deep-research run for a case."""
    scan_id = start_deep_research(
        case_id,
        question=question,
        depth=depth,
        max_sources=max_sources,
        max_subqueries=max_subqueries,
    )
    return {"scan_id": scan_id, "status": "running", "mode": "deep_research"}


@mcp.tool()
def get_scan_filters(case_id: str) -> dict[str, Any]:
    """Get scan filter preferences (CCF rank / arXiv categories)."""
    with session_scope(SessionLocal) as session:
        case = session.get(ResearchCase, case_id)
        if case is None:
            return {"error": f"case not found: {case_id}"}
        filters = (case.settings_json or {}).get("scan_filters") or {}
        return {
            "ccf_rank": filters.get("ccf_rank") or None,
            "arxiv_categories": filters.get("arxiv_categories") or [],
        }


@mcp.tool()
def set_scan_filters(
    case_id: str,
    ccf_rank: str | None = None,
    arxiv_categories: list[str] | None = None,
) -> dict[str, Any]:
    """Persist scan filter preferences for a case."""
    categories = list(dict.fromkeys(cat.strip() for cat in (arxiv_categories or []) if cat.strip()))
    with session_scope(SessionLocal) as session:
        case = session.get(ResearchCase, case_id)
        if case is None:
            return {"error": f"case not found: {case_id}"}
        settings = dict(case.settings_json or {})
        settings["scan_filters"] = {
            "ccf_rank": ccf_rank or None,
            "arxiv_categories": categories,
        }
        case.settings_json = settings
        return settings["scan_filters"]


@mcp.tool()
def get_scan_webhook(case_id: str) -> dict[str, Any]:
    """Get scan completion webhook configuration for a case."""
    with session_scope(SessionLocal) as session:
        case = session.get(ResearchCase, case_id)
        if case is None:
            return {"error": f"case not found: {case_id}"}
        return {
            "enabled": False,
            "webhook_url": "",
            "notify_on": "all",
            **((case.settings_json or {}).get("scan_webhook") or {}),
        }


@mcp.tool()
def set_scan_webhook(
    case_id: str,
    enabled: bool,
    webhook_url: str = "",
    notify_on: str = "all",
) -> dict[str, Any]:
    """Enable or disable scan completion webhook notifications."""
    if notify_on not in {"completed", "failed", "all"}:
        raise ValueError("notify_on must be one of: completed, failed, all")

    with session_scope(SessionLocal) as session:
        case = session.get(ResearchCase, case_id)
        if case is None:
            return {"error": f"case not found: {case_id}"}
        settings = dict(case.settings_json or {})
        settings["scan_webhook"] = {
            "enabled": bool(enabled),
            "webhook_url": webhook_url.strip(),
            "notify_on": notify_on,
        }
        case.settings_json = settings
        return settings["scan_webhook"]


@mcp.tool()
def get_cost_alert(case_id: str) -> dict[str, Any]:
    """Get the cost-alert configuration for a case."""
    with session_scope(SessionLocal) as session:
        case = session.get(ResearchCase, case_id)
        if case is None:
            return {"error": f"case not found: {case_id}"}
        return {
            "enabled": False,
            "monthly_budget_usd": 0.0,
            "webhook_url": "",
            **(case.settings_json or {}).get("cost_alert", {}),
        }


@mcp.tool()
def set_cost_alert(
    case_id: str,
    enabled: bool,
    monthly_budget_usd: float = 0.0,
    webhook_url: str = "",
) -> dict[str, Any]:
    """Enable/disable monthly cost alert for a case."""
    with session_scope(SessionLocal) as session:
        case = session.get(ResearchCase, case_id)
        if case is None:
            return {"error": f"case not found: {case_id}"}
        settings = dict(case.settings_json or {})
        settings["cost_alert"] = {
            "enabled": bool(enabled),
            "monthly_budget_usd": max(0.0, float(monthly_budget_usd)),
            "webhook_url": webhook_url.strip(),
        }
        case.settings_json = settings
        return settings["cost_alert"]


@mcp.tool()
def test_cost_alert(case_id: str) -> dict[str, Any]:
    """Send a one-off cost-alert payload to the configured webhook for testing."""
    from radar.api import test_cost_alert as api_test_cost_alert

    return api_test_cost_alert(case_id)


@mcp.tool()
def execute_skill(
    case_id: str,
    action_type: str,
    skill_name: str = "",
    impact_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Execute a CCF-A skill (writer/reviewer/auditor/experiment/rebuttal/reference auditor)."""
    return SkillExecutionService(SessionLocal).execute(
        case_id,
        skill_name=skill_name,
        action_type=action_type,
        impact_ids=impact_ids or [],
    )


@mcp.tool()
def run_skill_pipeline(
    case_id: str,
    pipeline: str = "revision",
    skill_names: list[str] | None = None,
    impact_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Run a sequence of CCF-A skills in one request (revision/review/experiment)."""
    return SkillPipelineService(SessionLocal).run(
        case_id,
        pipeline=pipeline,
        skill_names=skill_names,
        impact_ids=impact_ids or [],
    )


@mcp.tool()
def get_auto_scan_config(case_id: str) -> dict[str, Any]:
    """Get the auto-scan configuration for a case."""
    return get_auto_scan(case_id, session_factory=SessionLocal)


@mcp.tool()
def set_auto_scan_config(
    case_id: str,
    enabled: bool,
    interval_hours: int = 168,
) -> dict[str, Any]:
    """Enable/disable periodic radar scans for a case."""
    return set_auto_scan(
        case_id,
        enabled=enabled,
        interval_hours=interval_hours,
        session_factory=SessionLocal,
    )


@mcp.tool()
def get_digest_webhook_config(case_id: str) -> dict[str, Any]:
    """Get scheduled digest webhook configuration."""
    return get_digest_webhook(case_id, session_factory=SessionLocal)


@mcp.tool()
def set_digest_webhook_config(
    case_id: str,
    enabled: bool,
    webhook_url: str = "",
    schedule: str = "weekly",
) -> dict[str, Any]:
    """Enable/disable scheduled digest webhook sending."""
    return set_digest_webhook(
        case_id,
        enabled=enabled,
        webhook_url=webhook_url,
        schedule=schedule,
        session_factory=SessionLocal,
    )


@mcp.tool()
def test_digest_webhook(case_id: str) -> dict[str, Any]:
    """Send a one-off digest to the configured webhook for testing."""
    import httpx

    from radar.services.digest_service import DigestService

    with session_scope(SessionLocal) as session:
        case = session.get(ResearchCase, case_id)
        if case is None:
            return {"error": f"case not found: {case_id}"}
        config = (case.settings_json or {}).get("digest_webhook") or {}
        webhook_url = config.get("webhook_url") or ""
        if not webhook_url:
            return {"error": "digest webhook URL not configured"}

    digest = DigestService(SessionLocal).generate_digest(case_id)
    try:
        response = httpx.post(
            webhook_url,
            json={
                "case_id": case_id,
                "title": digest["title"],
                "markdown": digest["markdown"],
                "sections": digest["sections"],
            },
            timeout=15,
        )
        response.raise_for_status()
    except Exception as exc:
        return {"error": f"webhook test failed: {exc}"}

    return {"sent": True, "webhook_url": webhook_url, "title": digest["title"]}


@mcp.tool()
def list_skill_runs(case_id: str) -> list[dict[str, Any]]:
    """Return recent CCF-A skill execution records for a case."""
    with session_scope(SessionLocal) as session:
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


@mcp.tool()
def list_skill_pipeline_runs(case_id: str) -> list[dict[str, Any]]:
    """Return recent CCF-A skill pipeline execution records."""
    with session_scope(SessionLocal) as session:
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


@mcp.tool()
def list_notifications(
    case_id: str, event_type: str | None = None
) -> list[dict[str, Any]]:
    """Return recent scheduler notification events (webhook sent/failed, auto-scan failures)."""
    allowed_types = [
        "cost_alert_webhook_sent",
        "cost_alert_webhook_failed",
        "auto_scan_start_failed",
    ]
    with session_scope(SessionLocal) as session:
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


@mcp.tool()
def import_source(
    case_id: str,
    url: str = "",
    doi: str = "",
) -> dict[str, Any]:
    """Import one external paper (arXiv URL / DOI) into a case."""
    return SourceImportService(SessionLocal).import_source(
        case_id, url=url, doi=doi
    )


@mcp.tool()
def list_impacts(case_id: str) -> list[dict[str, Any]]:
    """List impact-candidate papers for a case (with evidence and stance)."""
    from radar.api import _build_papers

    return _build_papers(case_id)


@mcp.tool()
def list_actions(case_id: str) -> list[dict[str, Any]]:
    """List active action items for a case."""
    items = ActionService(SessionLocal).list_actions(case_id)
    return [
        {
            "id": item.id,
            "action_type": item.action_type,
            "priority": item.priority,
            "title": item.title,
            "rationale": item.rationale,
            "checklist": item.checklist_json or [],
            "status": item.status,
            "due_label": item.due_label,
            "advice_source": item.advice_source,
        }
        for item in items
    ]


@mcp.tool()
def generate_digest(case_id: str, include_sections: list[str] | None = None) -> dict[str, Any]:
    """Generate a personalized weekly radar digest (Markdown + structured)."""
    return DigestService(SessionLocal).generate_digest(
        case_id, include_sections=include_sections
    )


@mcp.tool()
def ask_literature(case_id: str, question: str, top_k: int = 5) -> dict[str, Any]:
    """Ask a question over the papers already collected in a case."""
    return LiteratureQAService(SessionLocal).answer(case_id, question, top_k=top_k)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
