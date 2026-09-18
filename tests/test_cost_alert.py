"""Cost-alert webhook test-send endpoint coverage."""

from datetime import datetime, timezone

from starlette.testclient import TestClient

from radar.api import app
from radar.models import ModelRun, ResearchCase


def _use_test_database(monkeypatch, db_session_factory):
    import radar.api as api_module
    import radar.db as db_module

    engine = db_session_factory.kw["bind"]
    monkeypatch.setattr(api_module, "SessionLocal", db_session_factory)
    monkeypatch.setattr(db_module, "SessionLocal", db_session_factory)
    monkeypatch.setattr(db_module, "engine", engine)


def test_cost_alert_test_sends_current_monthly_cost(
    db_session_factory, golden_case, monkeypatch
):
    _use_test_database(monkeypatch, db_session_factory)

    with db_session_factory() as session:
        case = session.get(ResearchCase, golden_case)
        settings = dict(case.settings_json or {})
        settings["cost_alert"] = {
            "enabled": True,
            "monthly_budget_usd": 10.0,
            "webhook_url": "https://example.com/cost-hook",
        }
        case.settings_json = settings
        session.add(
            ModelRun(
                id="cost-alert-test-run",
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
                estimated_cost=4.25,
                created_at=datetime.now(timezone.utc),
            )
        )
        session.commit()

    calls = []

    class SuccessfulResponse:
        def raise_for_status(self):
            return None

    def fake_post(url, json=None, timeout=None):
        calls.append((url, json, timeout))
        return SuccessfulResponse()

    monkeypatch.setattr("radar.api.httpx.post", fake_post)

    with TestClient(app) as client:
        response = client.post(f"/api/cases/{golden_case}/cost-alert/test")

    assert response.status_code == 200, response.text
    assert response.json() == {
        "sent": True,
        "webhook_url": "https://example.com/cost-hook",
    }
    assert calls == [
        (
            "https://example.com/cost-hook",
            {
                "type": "cost_alert_test",
                "case_id": golden_case,
                "monthly_cost_usd": 4.25,
                "budget_usd": 10.0,
            },
            15,
        )
    ]


def test_cost_alert_test_returns_expected_errors(
    db_session_factory, golden_case, monkeypatch
):
    _use_test_database(monkeypatch, db_session_factory)

    with db_session_factory() as session:
        case = session.get(ResearchCase, golden_case)
        settings = dict(case.settings_json or {})
        settings["cost_alert"] = {"enabled": True, "monthly_budget_usd": 10.0}
        case.settings_json = settings
        session.commit()

    with TestClient(app) as client:
        missing_url = client.post(f"/api/cases/{golden_case}/cost-alert/test")
        missing_case = client.post("/api/cases/missing/cost-alert/test")

    assert missing_url.status_code == 400
    assert missing_case.status_code == 404

    with db_session_factory() as session:
        case = session.get(ResearchCase, golden_case)
        settings = dict(case.settings_json or {})
        settings["cost_alert"] = {
            "enabled": True,
            "monthly_budget_usd": 10.0,
            "webhook_url": "https://example.com/cost-hook",
        }
        case.settings_json = settings
        session.commit()

    def failing_post(*args, **kwargs):
        raise RuntimeError("webhook unavailable")

    monkeypatch.setattr("radar.api.httpx.post", failing_post)
    with TestClient(app) as client:
        failed = client.post(f"/api/cases/{golden_case}/cost-alert/test")

    assert failed.status_code == 502
