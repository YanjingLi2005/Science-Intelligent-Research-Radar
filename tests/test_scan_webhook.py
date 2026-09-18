"""Scan-completion webhook configuration and delivery tests."""

from datetime import datetime, timezone

from starlette.testclient import TestClient

from radar.api import app
from radar.models import ResearchCase, ScanRun
from radar.services.weekly_radar_service import WeeklyRadarService


def _use_test_database(monkeypatch, db_session_factory):
    import radar.api as api_module
    import radar.db as db_module

    engine = db_session_factory.kw["bind"]
    monkeypatch.setattr(api_module, "SessionLocal", db_session_factory)
    monkeypatch.setattr(db_module, "SessionLocal", db_session_factory)
    monkeypatch.setattr(db_module, "engine", engine)


def _finish_service(db_session_factory):
    service = WeeklyRadarService.__new__(WeeklyRadarService)
    service.session_factory = db_session_factory
    return service


def test_finish_scan_posts_configured_webhook(
    db_session_factory, golden_case, monkeypatch
):
    scan_id = "scan-webhook-completed"
    with db_session_factory() as session:
        case = session.get(ResearchCase, golden_case)
        settings = dict(case.settings_json or {})
        settings["scan_webhook"] = {
            "enabled": True,
            "webhook_url": "https://example.com/scan-hook",
            "notify_on": "completed",
        }
        case.settings_json = settings
        session.add(
            ScanRun(
                id=scan_id,
                case_id=golden_case,
                mode="live",
                status="running",
                started_at=datetime.now(timezone.utc),
                stats_json={"progress": {"value": 0.5}},
            )
        )
        session.commit()

    calls = []

    def fake_post(url, json=None, timeout=None):
        calls.append((url, json, timeout))

    monkeypatch.setattr(
        "radar.services.weekly_radar_service.httpx.post", fake_post
    )

    _finish_service(db_session_factory)._finish_scan(
        scan_id,
        status="completed",
        stats={"scanned_papers": 3},
        error_message=None,
    )

    assert len(calls) == 1
    assert calls[0][0] == "https://example.com/scan-hook"
    assert calls[0][1] == {
        "case_id": golden_case,
        "scan_id": scan_id,
        "status": "completed",
        "stats": {
            "progress": {"value": 0.5},
            "scanned_papers": 3,
        },
    }

    with db_session_factory() as session:
        scan = session.get(ScanRun, scan_id)
        assert scan.status == "completed"
        assert scan.finished_at is not None


def test_finish_scan_webhook_is_best_effort_and_filters_status(
    db_session_factory, golden_case, monkeypatch
):
    scan_id = "scan-webhook-failed-filtered"
    with db_session_factory() as session:
        case = session.get(ResearchCase, golden_case)
        settings = dict(case.settings_json or {})
        settings["scan_webhook"] = {
            "enabled": True,
            "webhook_url": "https://example.com/scan-hook",
            "notify_on": "completed",
        }
        case.settings_json = settings
        session.add(
            ScanRun(
                id=scan_id,
                case_id=golden_case,
                mode="live",
                status="running",
                started_at=datetime.now(timezone.utc),
                stats_json={},
            )
        )
        session.commit()

    def failing_post(*args, **kwargs):
        raise RuntimeError("webhook unavailable")

    monkeypatch.setattr(
        "radar.services.weekly_radar_service.httpx.post", failing_post
    )

    # A non-matching status does not send, and a failing send never escapes.
    _finish_service(db_session_factory)._finish_scan(
        scan_id,
        status="failed",
        stats={"error_count": 1},
        error_message="scan failed",
    )

    with db_session_factory() as session:
        scan = session.get(ScanRun, scan_id)
        assert scan.status == "failed"
        assert scan.error_message == "scan failed"


def test_scan_webhook_api_get_and_put(
    db_session_factory, golden_case, monkeypatch
):
    _use_test_database(monkeypatch, db_session_factory)

    with TestClient(app) as client:
        initial = client.get(f"/api/cases/{golden_case}/scan-webhook")
        assert initial.status_code == 200, initial.text
        assert initial.json() == {
            "enabled": False,
            "webhook_url": "",
            "notify_on": "all",
        }

        updated = client.put(
            f"/api/cases/{golden_case}/scan-webhook",
            json={
                "enabled": True,
                "webhook_url": "  https://example.com/scan-hook  ",
                "notify_on": "failed",
            },
        )
        assert updated.status_code == 200, updated.text
        assert updated.json() == {
            "enabled": True,
            "webhook_url": "https://example.com/scan-hook",
            "notify_on": "failed",
        }

        loaded = client.get(f"/api/cases/{golden_case}/scan-webhook")
        assert loaded.status_code == 200, loaded.text
        assert loaded.json() == updated.json()

