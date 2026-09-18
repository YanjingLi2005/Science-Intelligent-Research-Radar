"""SMTP notification configuration and test-send endpoint coverage."""

import smtplib

from starlette.testclient import TestClient

from radar.api import app
from radar.models import ResearchCase


def _use_test_database(monkeypatch, db_session_factory):
    import radar.api as api_module
    import radar.db as db_module

    engine = db_session_factory.kw["bind"]
    monkeypatch.setattr(api_module, "SessionLocal", db_session_factory)
    monkeypatch.setattr(db_module, "SessionLocal", db_session_factory)
    monkeypatch.setattr(db_module, "engine", engine)


def test_email_notify_get_put_masks_password(
    db_session_factory, golden_case, monkeypatch
):
    _use_test_database(monkeypatch, db_session_factory)

    with TestClient(app) as client:
        initial = client.get(f"/api/cases/{golden_case}/email-notify")
        assert initial.status_code == 200, initial.text
        assert initial.json() == {
            "enabled": False,
            "smtp_host": "",
            "smtp_port": 587,
            "username": "",
            "password": "",
            "recipient": "",
            "subject_prefix": "[Research Radar] ",
        }

        config = {
            "enabled": True,
            "smtp_host": "smtp.example.com",
            "smtp_port": 587,
            "username": "sender@example.com",
            "password": "smtp-secret",
            "recipient": "recipient@example.com",
            "subject_prefix": "[Radar] ",
        }
        updated = client.put(
            f"/api/cases/{golden_case}/email-notify", json=config
        )
        assert updated.status_code == 200, updated.text
        assert updated.json() == config

        loaded = client.get(f"/api/cases/{golden_case}/email-notify")
        assert loaded.status_code == 200, loaded.text
        assert loaded.json() == {**config, "password": "***"}

    with db_session_factory() as session:
        case = session.get(ResearchCase, golden_case)
        assert case.settings_json["email_notify"]["password"] == "smtp-secret"


def test_email_notify_put_masked_password_preserves_secret(
    db_session_factory, golden_case, monkeypatch
):
    _use_test_database(monkeypatch, db_session_factory)

    with TestClient(app) as client:
        config = {
            "enabled": True,
            "smtp_host": "smtp.example.com",
            "smtp_port": 587,
            "username": "sender@example.com",
            "password": "smtp-secret",
            "recipient": "recipient@example.com",
            "subject_prefix": "[Radar] ",
        }
        assert client.put(f"/api/cases/{golden_case}/email-notify", json=config).status_code == 200

        # UI sends the masked placeholder back on blur; the stored secret must survive.
        masked = {**config, "password": "***"}
        updated = client.put(f"/api/cases/{golden_case}/email-notify", json=masked)
        assert updated.status_code == 200, updated.text

        loaded = client.get(f"/api/cases/{golden_case}/email-notify")
        assert loaded.json()["password"] == "***"

    with db_session_factory() as session:
        case = session.get(ResearchCase, golden_case)
        assert case.settings_json["email_notify"]["password"] == "smtp-secret"


def test_email_notify_test_sends_mail_with_starttls(
    db_session_factory, golden_case, monkeypatch
):
    _use_test_database(monkeypatch, db_session_factory)

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

    class FakeSMTP:
        def __init__(self, host, port):
            calls.append(("connect", host, port))

        def starttls(self):
            calls.append(("starttls",))

        def login(self, username, password):
            calls.append(("login", username, password))

        def sendmail(self, sender, recipients, message):
            calls.append(("sendmail", sender, recipients, message))

        def quit(self):
            calls.append(("quit",))

    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)

    with TestClient(app) as client:
        response = client.post(f"/api/cases/{golden_case}/email-notify/test")

    assert response.status_code == 200, response.text
    assert response.json() == {
        "sent": True,
        "recipient": "recipient@example.com",
    }
    assert calls[:4] == [
        ("connect", "smtp.example.com", 587),
        ("starttls",),
        ("login", "sender@example.com", "smtp-secret"),
        ("sendmail", "sender@example.com", ["recipient@example.com"], calls[3][3]),
    ]
    assert "Subject: [Radar] SMTP test" in calls[3][3]
    assert calls[-1] == ("quit",)


def test_email_notify_test_requires_config_and_maps_smtp_errors(
    db_session_factory, golden_case, monkeypatch
):
    _use_test_database(monkeypatch, db_session_factory)

    with TestClient(app) as client:
        missing = client.post(f"/api/cases/{golden_case}/email-notify/test")

    assert missing.status_code == 400

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
        }
        case.settings_json = settings
        session.commit()

    def failing_smtp(*args, **kwargs):
        raise OSError("SMTP unavailable")

    monkeypatch.setattr(smtplib, "SMTP", failing_smtp)
    with TestClient(app) as client:
        failed = client.post(f"/api/cases/{golden_case}/email-notify/test")

    assert failed.status_code == 502
