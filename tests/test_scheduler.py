"""Auto-scan scheduler tests."""

from datetime import datetime, timedelta, timezone

from starlette.testclient import TestClient

from radar.api import app
from radar.models import ResearchCase
from radar.services.scheduler_service import (
    due_cases,
    get_auto_scan,
    run_due_scans,
    set_auto_scan,
)


def test_set_and_get_auto_scan_config(db_session_factory, golden_case):
    config = set_auto_scan(
        golden_case,
        enabled=True,
        interval_hours=24,
        session_factory=db_session_factory,
    )
    assert config["enabled"] is True
    assert config["interval_hours"] == 24
    assert config["next_run_at"]

    loaded = get_auto_scan(golden_case, session_factory=db_session_factory)
    assert loaded["enabled"] is True
    assert loaded["interval_hours"] == 24


def test_due_cases_returns_enabled_past_due(db_session_factory, golden_case):
    set_auto_scan(
        golden_case,
        enabled=True,
        interval_hours=24,
        next_run_at=(datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat(),
        session_factory=db_session_factory,
    )
    assert golden_case in due_cases(session_factory=db_session_factory)


def test_run_due_scans_starts_and_reschedules(db_session_factory, golden_case, monkeypatch):
    started: list[str] = []

    def fake_start(case_id, **kwargs):
        started.append(case_id)

    monkeypatch.setattr("radar.services.scheduler_service.start", fake_start)

    set_auto_scan(
        golden_case,
        enabled=True,
        interval_hours=24,
        next_run_at=(datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat(),
        session_factory=db_session_factory,
    )

    assert run_due_scans(session_factory=db_session_factory) == [golden_case]
    assert started == [golden_case]

    config = get_auto_scan(golden_case, session_factory=db_session_factory)
    next_at = datetime.fromisoformat(config["next_run_at"].replace("Z", "+00:00"))
    assert next_at > datetime.now(timezone.utc)


def test_run_due_scans_records_start_failure(db_session_factory, golden_case, monkeypatch):
    from radar.models import AuditEvent

    def failing_start(case_id, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr("radar.services.scheduler_service.start", failing_start)

    set_auto_scan(
        golden_case,
        enabled=True,
        interval_hours=24,
        next_run_at=(datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat(),
        session_factory=db_session_factory,
    )

    assert run_due_scans(session_factory=db_session_factory) == []
    config = get_auto_scan(golden_case, session_factory=db_session_factory)
    assert config["last_error"] == "RuntimeError: boom"
    assert config["last_error_at"]

    with db_session_factory() as session:
        event = session.query(AuditEvent).filter(
            AuditEvent.event_type == "auto_scan_start_failed"
        ).first()
        assert event is not None


def test_auto_scan_api_route(monkeypatch):
    import radar.services.scheduler_service as scheduler_module

    monkeypatch.setattr(
        scheduler_module,
        "get_auto_scan",
        lambda case_id, **kwargs: {
            "enabled": True,
            "interval_hours": 24,
            "next_run_at": "2026-01-01T00:00:00+00:00",
        },
    )

    with TestClient(app) as client:
        resp = client.get("/api/cases/whatever/auto-scan")
        assert resp.status_code == 200, resp.text
        assert resp.json()["enabled"] is True


def test_check_cost_alerts_sends_webhook_and_respects_cooldown(
    db_session_factory, golden_case, monkeypatch
):
    from datetime import datetime, timezone

    from radar.models import ModelRun
    from radar.services.scheduler_service import check_cost_alerts

    with db_session_factory() as session:
        case = session.get(ResearchCase, golden_case)
        settings = dict(case.settings_json or {})
        settings["cost_alert"] = {
            "enabled": True,
            "monthly_budget_usd": 1.0,
            "webhook_url": "http://example.com/hook",
        }
        case.settings_json = settings
        session.add(
            ModelRun(
                id="cost-run",
                stage="test",
                case_id=golden_case,
                provider="test",
                model="test",
                prompt_hash="hash",
                schema_version="v1",
                input_refs_json=[],
                raw_response="{}",
                parsed_output_json={},
                validation_json={},
                estimated_cost=5.0,
                created_at=datetime.now(timezone.utc),
            )
        )
        session.commit()

    calls = []

    def fake_post(url, json=None, timeout=None):
        calls.append((url, json))

    monkeypatch.setattr("radar.services.scheduler_service.httpx.post", fake_post)

    assert check_cost_alerts(session_factory=db_session_factory) == [golden_case]
    assert len(calls) == 1
    assert calls[0][0] == "http://example.com/hook"
    assert calls[0][1]["monthly_cost_usd"] == 5.0

    from radar.models import AuditEvent

    with db_session_factory() as session:
        event = session.query(AuditEvent).filter(
            AuditEvent.event_type == "cost_alert_webhook_sent"
        ).first()
        assert event is not None

    # Cooldown: second call does not notify again.
    assert check_cost_alerts(session_factory=db_session_factory) == []
    assert len(calls) == 1


def test_send_scheduled_digests_sends_webhook_and_respects_cadence(
    db_session_factory, golden_case, monkeypatch
):
    from radar.services.scheduler_service import send_scheduled_digests

    with db_session_factory() as session:
        case = session.get(ResearchCase, golden_case)
        settings = dict(case.settings_json or {})
        settings["digest_webhook"] = {
            "enabled": True,
            "webhook_url": "http://example.com/digest",
            "schedule": "weekly",
        }
        case.settings_json = settings
        session.commit()

    calls = []

    def fake_post(url, json=None, timeout=None):
        calls.append((url, json))

    monkeypatch.setattr("radar.services.scheduler_service.httpx.post", fake_post)
    monkeypatch.setattr(
        "radar.services.digest_service.DigestService.generate_digest",
        lambda self, case_id, include_sections=None: {
            "title": "digest",
            "markdown": "# digest",
            "sections": [],
        },
    )

    assert send_scheduled_digests(session_factory=db_session_factory) == [golden_case]
    assert len(calls) == 1
    assert calls[0][0] == "http://example.com/digest"

    # Cadence: second call does not send again.
    assert send_scheduled_digests(session_factory=db_session_factory) == []
    assert len(calls) == 1


def test_send_scheduled_digests_sends_email_when_no_webhook(
    db_session_factory, golden_case, monkeypatch
):
    from radar.services.scheduler_service import send_scheduled_digests

    with db_session_factory() as session:
        case = session.get(ResearchCase, golden_case)
        settings = dict(case.settings_json or {})
        settings["email_notify"] = {
            "enabled": True,
            "smtp_host": "smtp.example.com",
            "smtp_port": 587,
            "username": "sender@example.com",
            "password": "smtp-secret",
            "recipient": "recipient@example.com",
            "subject_prefix": "[Radar] ",
        }
        case.settings_json = settings
        session.commit()

    calls = []

    def fake_send_email(config, subject, body):
        calls.append((config, subject, body))

    monkeypatch.setattr(
        "radar.services.email_service.send_email", fake_send_email
    )
    monkeypatch.setattr(
        "radar.services.digest_service.DigestService.generate_digest",
        lambda self, case_id, include_sections=None: {
            "title": "digest title",
            "markdown": "# digest",
            "sections": [],
        },
    )

    assert send_scheduled_digests(session_factory=db_session_factory) == [golden_case]
    assert len(calls) == 1
    assert calls[0][1] == "digest title"
    assert calls[0][2] == "# digest"

    with db_session_factory() as session:
        case = session.get(ResearchCase, golden_case)
        assert case.settings_json["email_notify"]["last_sent_at"]
