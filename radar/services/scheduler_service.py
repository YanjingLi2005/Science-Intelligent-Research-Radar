"""Lightweight auto-scan scheduler for continuous literature monitoring.

Stores per-case auto-scan config in ``ResearchCase.settings_json["auto_scan"]``
and starts due radar scans through the same background runner the UI uses.
"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from radar.db import SessionLocal, session_scope
from radar.models import AuditEvent, ModelRun, ResearchCase
from radar.services.scan_runner import ScanAlreadyRunningError, start

DEFAULT_INTERVAL_HOURS = 24 * 7
MIN_INTERVAL_HOURS = 1
MAX_INTERVAL_HOURS = 24 * 30


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def get_auto_scan(
    case_id: str,
    *,
    session_factory: sessionmaker[Session] = SessionLocal,
) -> dict[str, Any]:
    with session_scope(session_factory) as session:
        case = session.get(ResearchCase, case_id)
        if case is None:
            raise LookupError(f"case not found: {case_id}")
        return {
            "enabled": False,
            "interval_hours": DEFAULT_INTERVAL_HOURS,
            "next_run_at": None,
            "last_error": None,
            "last_error_at": None,
            **(case.settings_json or {}).get("auto_scan", {}),
        }


def set_auto_scan(
    case_id: str,
    *,
    enabled: bool,
    interval_hours: int = DEFAULT_INTERVAL_HOURS,
    next_run_at: str | None = None,
    session_factory: sessionmaker[Session] = SessionLocal,
) -> dict[str, Any]:
    interval_hours = max(MIN_INTERVAL_HOURS, min(int(interval_hours), MAX_INTERVAL_HOURS))
    with session_scope(session_factory) as session:
        case = session.get(ResearchCase, case_id)
        if case is None:
            raise LookupError(f"case not found: {case_id}")
        settings = dict(case.settings_json or {})
        if next_run_at is None:
            next_run_at = (
                (_now() + timedelta(hours=interval_hours)).isoformat()
                if enabled
                else None
            )
        config = {
            "enabled": bool(enabled),
            "interval_hours": interval_hours,
            "next_run_at": next_run_at,
            "last_error": None,
            "last_error_at": None,
        }
        settings["auto_scan"] = config
        case.settings_json = settings
        return config


def due_cases(
    *,
    now: datetime | None = None,
    session_factory: sessionmaker[Session] = SessionLocal,
) -> list[str]:
    now = now or _now()
    with session_scope(session_factory) as session:
        cases = list(session.scalars(select(ResearchCase)))
        result: list[str] = []
        for case in cases:
            config = (case.settings_json or {}).get("auto_scan") or {}
            if not config.get("enabled"):
                continue
            due_at = _parse_dt(config.get("next_run_at"))
            if due_at is not None and due_at <= now:
                result.append(case.id)
        return result


def run_due_scans(
    *,
    now: datetime | None = None,
    session_factory: sessionmaker[Session] = SessionLocal,
    limit: int = 5,
) -> list[str]:
    started: list[str] = []
    for case_id in due_cases(now=now, session_factory=session_factory)[:limit]:
        try:
            start(case_id, session_factory=session_factory)
        except ScanAlreadyRunningError:
            # A case with an active scan simply waits for the next cycle.
            continue
        except Exception as exc:
            _record_start_failure(case_id, exc, session_factory=session_factory)
            continue
        with session_scope(session_factory) as session:
            case = session.get(ResearchCase, case_id)
            if case is not None:
                settings = dict(case.settings_json or {})
                config = dict(settings.get("auto_scan") or {})
                interval = int(config.get("interval_hours", DEFAULT_INTERVAL_HOURS))
                config["next_run_at"] = (
                    _now() + timedelta(hours=interval)
                ).isoformat()
                settings["auto_scan"] = config
                case.settings_json = settings
        started.append(case_id)
    return started


def _record_start_failure(
    case_id: str,
    exc: Exception,
    *,
    session_factory: sessionmaker[Session] = SessionLocal,
) -> None:
    """Persist an auto-scan start failure on the case and in the audit trail."""
    error = f"{type(exc).__name__}: {exc}"
    now = _now()
    with session_scope(session_factory) as session:
        case = session.get(ResearchCase, case_id)
        if case is not None:
            settings = dict(case.settings_json or {})
            config = dict(settings.get("auto_scan") or {})
            config["last_error"] = error
            config["last_error_at"] = now.isoformat()
            settings["auto_scan"] = config
            case.settings_json = settings
        session.add(
            AuditEvent(
                id=str(uuid4()),
                case_id=case_id,
                event_type="auto_scan_start_failed",
                object_type="AutoScan",
                object_id=case_id,
                payload_json={"error": error},
                actor_type="scheduler",
                actor_id="auto_scan",
            )
        )


def check_cost_alerts(
    *,
    now: datetime | None = None,
    session_factory: sessionmaker[Session] = SessionLocal,
) -> list[str]:
    """Send webhook notifications for cases whose monthly cost exceeds budget.

    Each case is notified at most once per 24 hours. Failures are silent so a
    bad webhook never crashes the scheduler.
    """
    now = now or _now()
    cutoff = now - timedelta(days=30)
    notified: list[str] = []
    with session_scope(session_factory) as session:
        cases = list(session.scalars(select(ResearchCase)))
        for case in cases:
            config = (case.settings_json or {}).get("cost_alert") or {}
            if not config.get("enabled") or not config.get("webhook_url"):
                continue
            budget = float(config.get("monthly_budget_usd", 0.0))
            if budget <= 0:
                continue
            monthly = float(
                session.scalar(
                    select(func.sum(ModelRun.estimated_cost)).where(
                        ModelRun.case_id == case.id,
                        ModelRun.created_at >= cutoff,
                    )
                )
                or 0.0
            )
            if monthly <= budget:
                continue
            last = config.get("last_notified_at")
            if last:
                try:
                    last_dt = datetime.fromisoformat(str(last).replace("Z", "+00:00"))
                    if last_dt.tzinfo is None:
                        last_dt = last_dt.replace(tzinfo=timezone.utc)
                except ValueError:
                    last_dt = None
                if last_dt is not None and (now - last_dt) < timedelta(hours=24):
                    continue
            try:
                httpx.post(
                    str(config["webhook_url"]),
                    json={
                        "case_id": case.id,
                        "title": case.title,
                        "monthly_cost_usd": round(monthly, 4),
                        "budget_usd": budget,
                    },
                    timeout=10,
                )
            except Exception as exc:
                session.add(
                    AuditEvent(
                        id=str(uuid4()),
                        case_id=case.id,
                        event_type="cost_alert_webhook_failed",
                        object_type="CostAlert",
                        object_id=case.id,
                        payload_json={
                            "webhook_url": str(config["webhook_url"]),
                            "error": str(exc),
                        },
                        actor_type="scheduler",
                        actor_id="cost_alert",
                    )
                )
                continue
            session.add(
                AuditEvent(
                    id=str(uuid4()),
                    case_id=case.id,
                    event_type="cost_alert_webhook_sent",
                    object_type="CostAlert",
                    object_id=case.id,
                    payload_json={
                        "webhook_url": str(config["webhook_url"]),
                        "monthly_cost_usd": round(monthly, 4),
                        "budget_usd": budget,
                    },
                    actor_type="scheduler",
                    actor_id="cost_alert",
                )
            )
            settings = dict(case.settings_json or {})
            alert = dict(settings.get("cost_alert") or {})
            alert["last_notified_at"] = now.isoformat()
            settings["cost_alert"] = alert
            case.settings_json = settings
            notified.append(case.id)
    return notified


def get_digest_webhook(
    case_id: str,
    *,
    session_factory: sessionmaker[Session] = SessionLocal,
) -> dict[str, Any]:
    with session_scope(session_factory) as session:
        case = session.get(ResearchCase, case_id)
        if case is None:
            raise LookupError(f"case not found: {case_id}")
        return {
            "enabled": False,
            "webhook_url": "",
            "schedule": "weekly",
            **(case.settings_json or {}).get("digest_webhook", {}),
        }


def set_digest_webhook(
    case_id: str,
    *,
    enabled: bool,
    webhook_url: str = "",
    schedule: str = "weekly",
    session_factory: sessionmaker[Session] = SessionLocal,
) -> dict[str, Any]:
    schedule = schedule if schedule in {"daily", "weekly", "monthly"} else "weekly"
    with session_scope(session_factory) as session:
        case = session.get(ResearchCase, case_id)
        if case is None:
            raise LookupError(f"case not found: {case_id}")
        settings = dict(case.settings_json or {})
        settings["digest_webhook"] = {
            "enabled": bool(enabled),
            "webhook_url": webhook_url.strip(),
            "schedule": schedule,
        }
        case.settings_json = settings
        return settings["digest_webhook"]


def send_scheduled_digests(
    *,
    now: datetime | None = None,
    session_factory: sessionmaker[Session] = SessionLocal,
) -> list[str]:
    """Send scheduled radar digests to configured webhooks and/or email.

    Daily/weekly/monthly cadence is based on ``last_sent_at``; a case with no
    ``last_sent_at`` is sent on the next scheduler tick. If a case only has
    SMTP email configured, the digest is delivered by email.
    """
    now = now or _now()
    schedule_hours = {"daily": 24, "weekly": 168, "monthly": 720}
    sent: list[str] = []
    with session_scope(session_factory) as session:
        cases = list(session.scalars(select(ResearchCase)))
        for case in cases:
            settings = dict(case.settings_json or {})
            webhook_config = settings.get("digest_webhook") or {}
            email_config = settings.get("email_notify") or {}
            webhook_enabled = bool(
                webhook_config.get("enabled") and webhook_config.get("webhook_url")
            )
            email_enabled = bool(
                email_config.get("enabled") and email_config.get("smtp_host")
            )
            if not webhook_enabled and not email_enabled:
                continue

            # The digest cadence lives on the webhook config today; email-only
            # setups fall back to weekly unless a schedule was stored there.
            schedule = webhook_config.get("schedule", "weekly")
            if not schedule:
                schedule = "weekly"
            interval = schedule_hours.get(schedule, 168)
            last = webhook_config.get("last_sent_at") or email_config.get(
                "last_sent_at"
            )
            if last:
                try:
                    last_dt = datetime.fromisoformat(str(last).replace("Z", "+00:00"))
                    if last_dt.tzinfo is None:
                        last_dt = last_dt.replace(tzinfo=timezone.utc)
                except ValueError:
                    last_dt = None
                if last_dt is not None and (now - last_dt) < timedelta(hours=interval):
                    continue

            try:
                from radar.services.digest_service import DigestService

                digest = DigestService(session_factory).generate_digest(case.id)
            except Exception:
                continue

            if webhook_enabled:
                try:
                    httpx.post(
                        str(webhook_config["webhook_url"]),
                        json={
                            "case_id": case.id,
                            "title": digest["title"],
                            "markdown": digest["markdown"],
                            "sections": digest["sections"],
                        },
                        timeout=15,
                    )
                except Exception:
                    pass
            if email_enabled:
                try:
                    from radar.services.email_service import send_email

                    send_email(
                        email_config,
                        digest["title"],
                        digest["markdown"],
                    )
                except Exception:
                    pass

            settings = dict(case.settings_json or {})
            if webhook_config:
                hook = dict(settings.get("digest_webhook") or {})
                hook["last_sent_at"] = now.isoformat()
                settings["digest_webhook"] = hook
            if email_config:
                mail = dict(settings.get("email_notify") or {})
                mail["last_sent_at"] = now.isoformat()
                settings["email_notify"] = mail
            case.settings_json = settings
            sent.append(case.id)
    return sent


def start_scheduler(
    *,
    interval_seconds: float = 60.0,
    session_factory: sessionmaker[Session] = SessionLocal,
) -> threading.Thread:
    """Start a daemon scheduler loop. Intended for the FastAPI lifespan."""

    def loop() -> None:
        while True:
            try:
                run_due_scans(session_factory=session_factory)
            except Exception:
                # Scheduler failures must never crash the web process.
                pass
            try:
                check_cost_alerts(session_factory=session_factory)
            except Exception:
                pass
            try:
                send_scheduled_digests(session_factory=session_factory)
            except Exception:
                pass
            time.sleep(interval_seconds)

    thread = threading.Thread(
        target=loop, name="radar-auto-scan-scheduler", daemon=True
    )
    thread.start()
    return thread
