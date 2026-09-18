"""Multi-user auth and per-tenant data/settings isolation."""

import os

import pytest
from starlette.testclient import TestClient

import radar.api as api_module
import radar.auth as auth_module
import radar.tenant as tenant_module


@pytest.fixture
def auth_enabled(monkeypatch, tmp_path):
    """Point the accounts DB and per-user data dir at a temp location and turn
    the auth gate back on for the duration of the test."""
    monkeypatch.delenv("RADAR_AUTH_DISABLED", raising=False)
    monkeypatch.setattr(auth_module, "_ACCOUNTS_DB", tmp_path / "users.db")
    monkeypatch.setattr(
        tenant_module, "_users_root", lambda: tmp_path / "users"
    )
    api_module._AUTH_ATTEMPTS.clear()
    return tmp_path


def _client():
    return TestClient(api_module.app)


def _register(client, username="alice", password="password123"):
    resp = client.post(
        "/api/auth/register", json={"username": username, "password": password}
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _login(client, username="alice", password="password123"):
    resp = client.post(
        "/api/auth/login", json={"username": username, "password": password}
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["token"]


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def test_register_login_me_logout(auth_enabled):
    client = _client()
    _register(client)
    token = _login(client)

    me = client.get("/api/auth/me", headers=_auth(token))
    assert me.status_code == 200
    assert me.json()["username"] == "alice"

    logout = client.post("/api/auth/logout", headers=_auth(token))
    assert logout.status_code == 200

    me_after = client.get("/api/auth/me", headers=_auth(token))
    assert me_after.status_code == 401


def test_register_duplicate_and_bad_credentials(auth_enabled):
    client = _client()
    _register(client, "bob")
    dup = client.post(
        "/api/auth/register", json={"username": "bob", "password": "password123"}
    )
    assert dup.status_code == 400

    bad_short = client.post(
        "/api/auth/register", json={"username": "carol", "password": "short"}
    )
    assert bad_short.status_code == 400

    wrong = client.post(
        "/api/auth/login", json={"username": "bob", "password": "nope-nope-nope"}
    )
    assert wrong.status_code == 401


def test_api_requires_token_when_auth_enabled(auth_enabled):
    client = _client()
    resp = client.get("/api/cases")
    assert resp.status_code == 401

    garbage = client.get("/api/cases", headers=_auth("not-a-real-token"))
    assert garbage.status_code == 401


def test_users_see_only_their_own_cases(auth_enabled):
    client = _client()
    _register(client, "alice")
    _register(client, "bob")
    alice_token = _login(client, "alice")
    bob_token = _login(client, "bob")

    alice_cases = client.get("/api/cases", headers=_auth(alice_token)).json()
    assert alice_cases == []

    # Alice creates a case (minimal upload of a tex manuscript).
    import io

    files = {
        "manuscript": (
            "a.tex",
            b"\\documentclass{article}\n\\title{T}\n\\begin{document}\n"
            b"\\section{Introduction}\n"
            b"We study retrieval augmentation and propose a novel method.\n"
            b"\\section{Results}\n"
            b"Our method improves accuracy from 50% to 90% on the benchmark, "
            b"reducing latency by 30% while keeping the model small enough to "
            b"deploy on a single GPU with batch size 32 and no quality loss.\n"
            b"\\section{Conclusion}\n"
            b"We demonstrate strong gains across three domains and settings.\n"
            b"\\end{document}\n",
            "application/octet-stream",
        )
    }
    created = client.post(
        "/api/cases",
        data={"title": "Alice's paper", "research_question": "rq"},
        files=files,
        headers=_auth(alice_token),
    )
    assert created.status_code == 200, created.text
    alice_id = created.json()["id"]

    alice_cases = client.get("/api/cases", headers=_auth(alice_token)).json()
    assert [c["id"] for c in alice_cases] == [alice_id]

    # Bob must not see Alice's case.
    bob_cases = client.get("/api/cases", headers=_auth(bob_token)).json()
    assert alice_id not in [c["id"] for c in bob_cases]
    assert bob_cases == []

    # Bob cannot fetch Alice's case detail either.
    detail = client.get(f"/api/cases/{alice_id}", headers=_auth(bob_token))
    assert detail.status_code == 404


def test_llm_settings_are_per_user(auth_enabled):
    client = _client()
    _register(client, "alice")
    _register(client, "bob")
    alice_token = _login(client, "alice")
    bob_token = _login(client, "bob")

    save = client.put(
        "/api/settings",
        json={
            "updates": {
                "LLM_API_KEY": "sk-" + "a" * 40,
                "LLM_MODEL": "deepseek-v4-pro",
                "LLM_BASE_URL": "https://api.deepseek.com",
            }
        },
        headers=_auth(alice_token),
    )
    assert save.status_code == 200, save.text

    alice_settings = client.get("/api/settings", headers=_auth(alice_token)).json()
    assert alice_settings["llm"]["has_api_key"] is True
    assert alice_settings["llm"]["model"] == "deepseek-v4-pro"

    # Bob still has no LLM configured.
    bob_settings = client.get("/api/settings", headers=_auth(bob_token)).json()
    assert bob_settings["llm"]["has_api_key"] is False
    assert bob_settings["llm"]["model"] is None


def test_registration_can_be_disabled(auth_enabled, monkeypatch):
    client = _client()

    class _ClosedSettings:
        allow_registration = False

    monkeypatch.setattr(api_module, "get_settings", lambda: _ClosedSettings())
    resp = client.post(
        "/api/auth/register",
        json={"username": "newuser", "password": "password123"},
    )
    assert resp.status_code == 403
    assert "关闭公开注册" in resp.json()["detail"]


def test_admin_can_create_user_when_registration_disabled(auth_enabled, monkeypatch):
    client = _client()
    _register(client, "adminuser")
    admin_token = _login(client, "adminuser")

    class _ClosedSettings:
        allow_registration = False

    monkeypatch.setattr(api_module, "get_settings", lambda: _ClosedSettings())

    # Public registration stays closed.
    blocked = client.post(
        "/api/auth/register",
        json={"username": "newuser", "password": "password123"},
    )
    assert blocked.status_code == 403

    created = client.post(
        "/api/admin/users",
        json={"username": "newuser", "password": "password123"},
        headers=_auth(admin_token),
    )
    assert created.status_code == 200, created.text
    assert created.json()["role"] == "user"

    # The admin-created account can log in normally.
    token = _login(client, "newuser")
    me = client.get("/api/auth/me", headers=_auth(token))
    assert me.status_code == 200
    assert me.json()["username"] == "newuser"


def test_login_rate_limit_returns_429(auth_enabled, monkeypatch):
    client = _client()
    monkeypatch.setattr(api_module, "_AUTH_RATE_MAX_ATTEMPTS", 2)
    api_module._AUTH_ATTEMPTS.clear()
    for _ in range(2):
        resp = client.post(
            "/api/auth/login",
            json={"username": "nobody", "password": "wrong-password"},
        )
        assert resp.status_code == 401
    resp = client.post(
        "/api/auth/login",
        json={"username": "nobody", "password": "wrong-password"},
    )
    assert resp.status_code == 429
    assert "频繁" in resp.json()["detail"]
