"""Radar scan runs, scheduling, impact candidates, action items, and webhooks."""

from __future__ import annotations

from typing import Any
import httpx
from fastapi import APIRouter, HTTPException
from sqlalchemy import func, select

from radar.api_schemas import (
    _parse_datetime,
    ActionItemOut,
    ActionStatusRequest,
    AutoScanRequest,
    CostAlertRequest,
    DigestWebhookRequest,
    EditImpactRequest,
    EmailNotifyRequest,
    PaperOut,
    ScanFiltersRequest,
    ScanStartRequest,
    ScanWebhookRequest,
)
from radar.db import get_session_factory, session_scope
from radar.models import ActionItem, AuditEvent, ImpactCandidate, ModelRun, ResearchCase, ScanRun
from radar.services.action_service import ActionService
from radar.services.review_service import ReviewService
from radar.services.scan_runner import (
    ScanAlreadyRunningError,
    request_cancel,
    start,
)
from radar.routes.builders import _action_to_out, _build_papers, _impact_to_paper_out
from radar.routes.deps import _service

def _httpx_post(*args, **kwargs):
    try:
        import radar.api as api
        post_fn = getattr(getattr(api, "httpx", httpx), "post", httpx.post)
        return post_fn(*args, **kwargs)
    except Exception:
        return httpx.post(*args, **kwargs)


router = APIRouter(tags=["scans"])


def _current_monthly_cost(session: Any, case_id: str) -> float:
    """Return the cost recorded for a case during the current 30-day window."""
    from datetime import datetime, timedelta, timezone

    cutoff = datetime.now(timezone.utc) - timedelta(days=30)
    return round(
        float(
            session.scalar(
                select(func.sum(ModelRun.estimated_cost)).where(
                    ModelRun.case_id == case_id,
                    ModelRun.created_at >= cutoff,
                )
            )
            or 0.0
        ),
        4,
    )


@router.post("/api/cases/{case_id}/scans")
def start_scan(case_id: str, body: ScanStartRequest = ScanStartRequest()) -> dict[str, Any]:
    """Launch a background radar scan for this case."""
    try:
        scan_id = start(
            case_id,
            max_results=body.max_results,
            analysis_limit=body.analysis_limit,
        )
    except ScanAlreadyRunningError as exc:
        raise HTTPException(409, str(exc))
    return {"scan_id": scan_id, "status": "running"}


@router.get("/api/cases/{case_id}/scans")
def list_scans(case_id: str) -> list[dict[str, Any]]:
    """Return all scan runs for a case (most recent first)."""
    with session_scope() as session:
        scans = list(
            session.scalars(
                select(ScanRun)
                .where(ScanRun.case_id == case_id)
                .order_by(ScanRun.created_at.desc())
            )
        )
        scan_ids = [scan.id for scan in scans]
        cost_map: dict[str, float] = {}
        if scan_ids:
            cost_rows = session.execute(
                select(
                    ModelRun.scan_run_id,
                    func.sum(ModelRun.estimated_cost),
                )
                .where(ModelRun.scan_run_id.in_(scan_ids))
                .group_by(ModelRun.scan_run_id)
            ).all()
            cost_map = {
                str(row[0]): float(row[1] or 0.0) for row in cost_rows if row[0]
            }
        result = []
        for scan in scans:
            result.append({
                "id": scan.id,
                "mode": scan.mode,
                "status": scan.status,
                "started_at": _parse_datetime(scan.started_at),
                "finished_at": _parse_datetime(scan.finished_at),
                "progress": (scan.stats_json or {}).get("progress", {}),
                "stats": {k: v for k, v in (scan.stats_json or {}).items() if k != "progress"},
                "error_message": scan.error_message,
                "estimated_cost": cost_map.get(scan.id, 0.0),
            })
        return result


@router.get("/api/cases/{case_id}/scans/{scan_id}")
def get_scan_status(case_id: str, scan_id: str) -> dict[str, Any]:
    """Poll the status and progress of one scan run."""
    with session_scope() as session:
        scan = session.get(ScanRun, scan_id)
        if scan is None:
            raise HTTPException(404, f"scan not found: {scan_id}")
        return {
            "id": scan.id,
            "mode": scan.mode,
            "status": scan.status,
            "started_at": _parse_datetime(scan.started_at),
            "finished_at": _parse_datetime(scan.finished_at),
            "progress": (scan.stats_json or {}).get("progress", {}),
            "stats": {k: v for k, v in (scan.stats_json or {}).items() if k != "progress"},
            "error_message": scan.error_message,
        }


@router.delete("/api/cases/{case_id}/scans/{scan_id}")
def cancel_scan(case_id: str, scan_id: str) -> dict[str, Any]:
    """Request cancellation of a running scan."""
    try:
        accepted = request_cancel(scan_id)
    except LookupError:
        raise HTTPException(404, f"scan not found: {scan_id}")
    return {"scan_id": scan_id, "cancelled": accepted}


@router.get("/api/cases/{case_id}/auto-scan")
def get_auto_scan(case_id: str) -> dict[str, Any]:
    """Return the auto-scan configuration for a case."""
    from radar.services.scheduler_service import get_auto_scan as _get_auto_scan

    try:
        return _get_auto_scan(case_id, session_factory=get_session_factory())
    except LookupError as exc:
        raise HTTPException(404, str(exc))


@router.put("/api/cases/{case_id}/auto-scan")
def set_auto_scan(case_id: str, body: AutoScanRequest) -> dict[str, Any]:
    """Enable/disable periodic radar scans for a case."""
    from radar.services.scheduler_service import set_auto_scan as _set_auto_scan

    try:
        return _set_auto_scan(
            case_id,
            enabled=body.enabled,
            interval_hours=body.interval_hours,
            next_run_at=body.next_run_at,
            session_factory=get_session_factory(),
        )
    except LookupError as exc:
        raise HTTPException(404, str(exc))


@router.get("/api/cases/{case_id}/cost-alert")
def get_cost_alert(case_id: str) -> dict[str, Any]:
    """Return the cost-alert configuration for a case."""
    with session_scope(get_session_factory()) as session:
        case = session.get(ResearchCase, case_id)
        if case is None:
            raise HTTPException(404, f"case not found: {case_id}")
        return {
            "enabled": False,
            "monthly_budget_usd": 0.0,
            "webhook_url": "",
            **(case.settings_json or {}).get("cost_alert", {}),
        }


@router.put("/api/cases/{case_id}/cost-alert")
def set_cost_alert(case_id: str, body: CostAlertRequest) -> dict[str, Any]:
    """Enable/disable monthly cost alert for a case."""
    with session_scope(get_session_factory()) as session:
        case = session.get(ResearchCase, case_id)
        if case is None:
            raise HTTPException(404, f"case not found: {case_id}")
        settings = dict(case.settings_json or {})
        settings["cost_alert"] = {
            "enabled": bool(body.enabled),
            "monthly_budget_usd": max(0.0, float(body.monthly_budget_usd)),
            "webhook_url": (body.webhook_url or "").strip(),
        }
        case.settings_json = settings
        return settings["cost_alert"]


@router.post("/api/cases/{case_id}/cost-alert/test")
def test_cost_alert(case_id: str) -> dict[str, Any]:
    """Send a one-off cost-alert payload to the configured webhook."""
    with session_scope(get_session_factory()) as session:
        case = session.get(ResearchCase, case_id)
        if case is None:
            raise HTTPException(404, f"case not found: {case_id}")
        config = (case.settings_json or {}).get("cost_alert") or {}
        webhook_url = str(config.get("webhook_url") or "").strip()
        if not webhook_url:
            raise HTTPException(400, "cost alert webhook URL not configured")
        monthly_cost = _current_monthly_cost(session, case_id)
        budget = float(config.get("monthly_budget_usd") or 0.0)

    try:
        response = _httpx_post(
            webhook_url,
            json={
                "type": "cost_alert_test",
                "case_id": case_id,
                "monthly_cost_usd": monthly_cost,
                "budget_usd": budget,
            },
            timeout=15,
        )
        response.raise_for_status()
    except Exception as exc:
        raise HTTPException(502, f"webhook test failed: {exc}")

    return {"sent": True, "webhook_url": webhook_url}


@router.get("/api/cases/{case_id}/digest")
def get_digest(
    case_id: str, include_sections: str | None = None
) -> dict[str, Any]:
    """Generate a personalized weekly radar digest (Markdown + structured)."""
    from radar.services.digest_service import DigestService

    sections = (
        [item.strip() for item in include_sections.split(",") if item.strip()]
        if include_sections
        else None
    )
    try:
        return _service(DigestService).generate_digest(
            case_id, include_sections=sections
        )
    except LookupError as exc:
        raise HTTPException(404, str(exc))


@router.get("/api/cases/{case_id}/digest-webhook")
def get_digest_webhook(case_id: str) -> dict[str, Any]:
    """Return the scheduled digest webhook configuration."""
    with session_scope(get_session_factory()) as session:
        case = session.get(ResearchCase, case_id)
        if case is None:
            raise HTTPException(404, f"case not found: {case_id}")
        return {
            "enabled": False,
            "webhook_url": "",
            "schedule": "weekly",
            **(case.settings_json or {}).get("digest_webhook", {}),
        }


@router.put("/api/cases/{case_id}/digest-webhook")
def set_digest_webhook(case_id: str, body: DigestWebhookRequest) -> dict[str, Any]:
    """Enable/disable scheduled digest webhook sending."""
    schedule = body.schedule if body.schedule in {"daily", "weekly", "monthly"} else "weekly"
    with session_scope(get_session_factory()) as session:
        case = session.get(ResearchCase, case_id)
        if case is None:
            raise HTTPException(404, f"case not found: {case_id}")
        settings = dict(case.settings_json or {})
        settings["digest_webhook"] = {
            "enabled": bool(body.enabled),
            "webhook_url": (body.webhook_url or "").strip(),
            "schedule": schedule,
        }
        case.settings_json = settings
        return settings["digest_webhook"]


@router.post("/api/cases/{case_id}/digest-webhook/test")
def test_digest_webhook(case_id: str) -> dict[str, Any]:
    """Send a one-off digest to the configured webhook for testing."""
    from radar.services.digest_service import DigestService

    with session_scope(get_session_factory()) as session:
        case = session.get(ResearchCase, case_id)
        if case is None:
            raise HTTPException(404, f"case not found: {case_id}")
        config = (case.settings_json or {}).get("digest_webhook") or {}
        webhook_url = config.get("webhook_url") or ""
        if not webhook_url:
            raise HTTPException(400, "digest webhook URL not configured")

    digest = _service(DigestService).generate_digest(case_id)
    try:
        response = _httpx_post(
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
        raise HTTPException(502, f"webhook test failed: {exc}")

    return {"sent": True, "webhook_url": webhook_url, "title": digest["title"]}


@router.get("/api/cases/{case_id}/scan-webhook")
def get_scan_webhook(case_id: str) -> dict[str, Any]:
    """Return the webhook configuration for scan completion notifications."""
    with session_scope(get_session_factory()) as session:
        case = session.get(ResearchCase, case_id)
        if case is None:
            raise HTTPException(404, f"case not found: {case_id}")
        return {
            "enabled": False,
            "webhook_url": "",
            "notify_on": "all",
            **((case.settings_json or {}).get("scan_webhook") or {}),
        }


@router.put("/api/cases/{case_id}/scan-webhook")
def set_scan_webhook(case_id: str, body: ScanWebhookRequest) -> dict[str, Any]:
    """Enable or disable scan completion webhook notifications."""
    with session_scope(get_session_factory()) as session:
        case = session.get(ResearchCase, case_id)
        if case is None:
            raise HTTPException(404, f"case not found: {case_id}")
        settings = dict(case.settings_json or {})
        settings["scan_webhook"] = {
            "enabled": bool(body.enabled),
            "webhook_url": (body.webhook_url or "").strip(),
            "notify_on": body.notify_on,
        }
        case.settings_json = settings
        return settings["scan_webhook"]


@router.post("/api/cases/{case_id}/scan-webhook/test")
def test_scan_webhook(case_id: str) -> dict[str, Any]:
    """Send a one-off scan notification to the configured webhook."""
    with session_scope(get_session_factory()) as session:
        case = session.get(ResearchCase, case_id)
        if case is None:
            raise HTTPException(404, f"case not found: {case_id}")
        config = (case.settings_json or {}).get("scan_webhook") or {}
    webhook_url = str(config.get("webhook_url") or "").strip()
    if not webhook_url:
        raise HTTPException(400, "scan webhook URL is not configured")
    try:
        response = _httpx_post(
            webhook_url,
            json={"case_id": case_id, "scan_id": "test", "status": "test", "stats": {}},
            timeout=15,
        )
        response.raise_for_status()
    except Exception as exc:
        raise HTTPException(502, f"webhook test failed: {exc}")
    return {"sent": True, "webhook_url": webhook_url}


@router.get("/api/cases/{case_id}/email-notify")
def get_email_notify(case_id: str) -> dict[str, Any]:
    """Return the SMTP notification configuration for a case."""
    with session_scope(get_session_factory()) as session:
        case = session.get(ResearchCase, case_id)
        if case is None:
            raise HTTPException(404, f"case not found: {case_id}")

        stored = (case.settings_json or {}).get("email_notify") or {}
        config = {
            "enabled": False,
            "smtp_host": "",
            "smtp_port": 587,
            "username": "",
            "password": "",
            "recipient": "",
            "subject_prefix": "[Research Radar] ",
            **(stored if isinstance(stored, dict) else {}),
        }
        if config.get("password"):
            config["password"] = "***"
        return config


@router.put("/api/cases/{case_id}/email-notify")
def set_email_notify(case_id: str, body: EmailNotifyRequest) -> dict[str, Any]:
    """Store SMTP notification configuration for a case."""
    with session_scope(get_session_factory()) as session:
        case = session.get(ResearchCase, case_id)
        if case is None:
            raise HTTPException(404, f"case not found: {case_id}")
        settings = dict(case.settings_json or {})
        stored = settings.get("email_notify") or {}
        existing_password = (
            stored.get("password") if isinstance(stored, dict) else None
        )
        payload = body.model_dump()
        if existing_password and payload.get("password") in ("", "***"):
            payload["password"] = existing_password
        settings["email_notify"] = payload
        case.settings_json = settings
        return settings["email_notify"]


@router.post("/api/cases/{case_id}/email-notify/test")
def test_email_notify(case_id: str) -> dict[str, Any]:
    """Send a one-off SMTP test email using the case configuration."""
    with session_scope(get_session_factory()) as session:
        case = session.get(ResearchCase, case_id)
        if case is None:
            raise HTTPException(404, f"case not found: {case_id}")
        config = (case.settings_json or {}).get("email_notify") or {}
        if not isinstance(config, dict):
            config = {}

    required_fields = ("smtp_host", "username", "password", "recipient")
    missing = [
        field
        for field in required_fields
        if not str(config.get(field) or "").strip()
    ]
    if missing:
        raise HTTPException(
            400,
            "email notification is missing required fields: " + ", ".join(missing),
        )

    smtp_host = str(config["smtp_host"]).strip()
    smtp_port = int(config.get("smtp_port") or 587)
    username = str(config["username"]).strip()
    password = str(config["password"])
    recipient = str(config["recipient"]).strip()
    subject_prefix = str(config.get("subject_prefix") or "[Research Radar] ")

    from email.message import EmailMessage
    import smtplib

    message = EmailMessage()
    message["Subject"] = f"{subject_prefix}SMTP test"
    message["From"] = username
    message["To"] = recipient
    message.set_content(
        f"This is a test email from Research Radar for case {case_id}."
    )

    smtp = None
    try:
        if smtp_port == 465:
            smtp = smtplib.SMTP_SSL(smtp_host, smtp_port)
        else:
            smtp = smtplib.SMTP(smtp_host, smtp_port)
            smtp.starttls()
        smtp.login(username, password)
        smtp.sendmail(username, [recipient], message.as_string())
    except Exception as exc:
        raise HTTPException(502, f"email test failed: {exc}")
    finally:
        if smtp is not None:
            try:
                smtp.quit()
            except Exception:
                pass

    return {"sent": True, "recipient": recipient}


@router.get("/api/cases/{case_id}/monitoring-stats")
def get_monitoring_stats(case_id: str) -> dict[str, Any]:
    """Return monitoring health stats: scan counts, failures, last scan."""
    with session_scope(get_session_factory()) as session:
        case = session.get(ResearchCase, case_id)
        if case is None:
            raise HTTPException(404, f"case not found: {case_id}")
        scans = list(
            session.scalars(
                select(ScanRun).where(ScanRun.case_id == case_id)
            )
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
        monthly_cost = _current_monthly_cost(session, case_id)
        cost_alert = (case.settings_json or {}).get("cost_alert") or {}
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


@router.get("/api/cases/{case_id}/scan-filters")
def get_scan_filters(case_id: str) -> dict[str, Any]:
    """Return the scan filter preferences (CCF rank / arXiv categories)."""
    with session_scope(get_session_factory()) as session:
        case = session.get(ResearchCase, case_id)
        if case is None:
            raise HTTPException(404, f"case not found: {case_id}")
        filters = (case.settings_json or {}).get("scan_filters") or {}
        return {
            "ccf_rank": filters.get("ccf_rank") or None,
            "arxiv_categories": filters.get("arxiv_categories") or [],
        }


@router.put("/api/cases/{case_id}/scan-filters")
def set_scan_filters(case_id: str, body: ScanFiltersRequest) -> dict[str, Any]:
    """Persist scan filter preferences for a case."""
    with session_scope(get_session_factory()) as session:
        case = session.get(ResearchCase, case_id)
        if case is None:
            raise HTTPException(404, f"case not found: {case_id}")
        settings = dict(case.settings_json or {})
        settings["scan_filters"] = {
            "ccf_rank": body.ccf_rank or None,
            "arxiv_categories": list(dict.fromkeys(
                cat.strip() for cat in body.arxiv_categories if cat.strip()
            )),
        }
        case.settings_json = settings
        return settings["scan_filters"]


@router.get("/api/cases/{case_id}/impacts", response_model=list[PaperOut])
def list_impacts(case_id: str) -> list[dict[str, Any]]:
    """Return all impact-candidate papers for a case."""
    return _build_papers(case_id)


@router.post("/api/cases/{case_id}/impacts/{impact_id}/confirm")
def confirm_impact(case_id: str, impact_id: str) -> dict[str, Any]:
    """Confirm an impact candidate as accepted evidence."""
    try:
        impact = _service(ReviewService).confirm_impact(impact_id)
    except LookupError:
        raise HTTPException(404, f"impact not found: {impact_id}")
    return _impact_to_paper_out(impact).model_dump()


@router.post("/api/cases/{case_id}/impacts/{impact_id}/dismiss")
def dismiss_impact(case_id: str, impact_id: str) -> dict[str, Any]:
    """Dismiss an impact candidate."""
    try:
        impact = _service(ReviewService).dismiss_impact(impact_id)
    except LookupError:
        raise HTTPException(404, f"impact not found: {impact_id}")
    return _impact_to_paper_out(impact).model_dump()


@router.put("/api/cases/{case_id}/impacts/{impact_id}")
def edit_impact(case_id: str, impact_id: str, body: EditImpactRequest) -> dict[str, Any]:
    """Edit an impact candidate's metadata."""
    try:
        impact = _service(ReviewService).edit_impact(
            impact_id, body.model_dump(exclude_none=True)
        )
    except LookupError:
        raise HTTPException(404, f"impact not found: {impact_id}")
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return _impact_to_paper_out(impact).model_dump()


@router.get("/api/cases/{case_id}/actions", response_model=list[ActionItemOut])
def list_actions(case_id: str) -> list[dict[str, Any]]:
    """Return all active action items for a case."""
    items = _service(ActionService).list_actions(case_id)
    return [_action_to_out(item) for item in items]


@router.put("/api/cases/{case_id}/actions/{action_id}/status")
def update_action_status(case_id: str, action_id: str, body: ActionStatusRequest) -> dict[str, Any]:
    """Update an action item's status."""
    try:
        item = _service(ActionService).update_status(action_id, body.status)
    except LookupError:
        raise HTTPException(404, f"action not found: {action_id}")
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return _action_to_out(item)
