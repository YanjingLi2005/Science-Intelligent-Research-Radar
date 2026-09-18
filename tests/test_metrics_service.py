"""Cross-tenant metrics aggregation for the internal monitoring dashboard."""

import sqlite3
from pathlib import Path

from radar.services.metrics_service import _normalize_error, collect_metrics


def _make_product_db(path: Path, *, rows: dict) -> None:
    """Create a minimal product database with only the columns metrics read."""

    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE model_runs (
            stage TEXT, model TEXT, input_tokens INTEGER, output_tokens INTEGER,
            estimated_cost REAL, latency_ms INTEGER, created_at TEXT
        );
        CREATE TABLE scan_runs (
            status TEXT, started_at TEXT, finished_at TEXT,
            error_message TEXT, created_at TEXT
        );
        CREATE TABLE audit_events (event_type TEXT, created_at TEXT);
        """
    )
    connection.executemany(
        "INSERT INTO model_runs VALUES (?,?,?,?,?,?,?)", rows.get("model_runs", [])
    )
    connection.executemany(
        "INSERT INTO scan_runs VALUES (?,?,?,?,?)", rows.get("scan_runs", [])
    )
    connection.executemany(
        "INSERT INTO audit_events VALUES (?,?)", rows.get("audit_events", [])
    )
    connection.commit()
    connection.close()


def _make_accounts_db(path: Path, *, users: list, sessions: list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE users (id TEXT, username TEXT, created_at TEXT);
        CREATE TABLE sessions (token_hash TEXT, user_id TEXT, created_at TEXT);
        """
    )
    connection.executemany("INSERT INTO users VALUES (?,?,?)", users)
    connection.executemany("INSERT INTO sessions VALUES (?,?,?)", sessions)
    connection.commit()
    connection.close()


NOW = "2099-01-01 00:00:00"


def test_collect_metrics_sums_across_tenants(tmp_path):
    """Per-user databases must be folded into one deployment-wide view."""

    users_root = tmp_path / "users"
    _make_product_db(
        users_root / "alice" / "research_radar.db",
        rows={
            "model_runs": [("impact_assessment", "m1", 100, 50, 0.02, 1000, NOW)],
            "scan_runs": [
                ("completed", NOW, NOW, None, NOW),
                ("failed", NOW, NOW, "429 Too Many Requests", NOW),
            ],
            "audit_events": [("weekly_scan_failed", NOW)],
        },
    )
    _make_product_db(
        users_root / "bob" / "research_radar.db",
        rows={
            "model_runs": [("incoming_result", "m1", 200, 100, 0.03, 3000, NOW)],
            "scan_runs": [("completed", NOW, NOW, None, NOW)],
            "audit_events": [],
        },
    )

    result = collect_metrics(
        window_days=3650,
        accounts_db=tmp_path / "missing-users.db",
        users_root=users_root,
        legacy_db=tmp_path / "missing-legacy.db",
    )

    assert result["tenant_count"] == 2
    assert result["cost"]["total_usd"] == 0.05
    assert result["cost"]["total_tokens"] == 450
    assert result["cost"]["llm_calls"] == 2
    assert result["health"]["total_scans"] == 3
    # Two completed, one failed -> 2/3.
    assert result["health"]["success_rate"] == round(2 / 3, 4)
    assert result["problems"]["failed_scans"] == 1
    assert {"reason": "LLM 限流 (429)", "count": 1} in result["problems"]["top_errors"]


def test_cancelled_scans_excluded_from_success_rate(tmp_path):
    """Cancelling is user intent, not a defect: it must not dilute the rate."""

    users_root = tmp_path / "users"
    _make_product_db(
        users_root / "alice" / "research_radar.db",
        rows={
            "scan_runs": [
                ("completed", NOW, NOW, None, NOW),
                ("cancelled", NOW, NOW, "Scan cancelled by user.", NOW),
                ("cancelled", NOW, NOW, None, NOW),
            ]
        },
    )

    result = collect_metrics(
        window_days=3650,
        accounts_db=tmp_path / "none.db",
        users_root=users_root,
        legacy_db=tmp_path / "none.db",
    )

    assert result["health"]["total_scans"] == 3
    assert result["health"]["success_rate"] == 1.0


def test_unreadable_tenant_degrades_to_zero(tmp_path):
    """One broken database must not take the whole dashboard down."""

    users_root = tmp_path / "users"
    _make_product_db(
        users_root / "alice" / "research_radar.db",
        rows={"model_runs": [("stage", "m1", 10, 5, 0.01, 100, NOW)]},
    )
    # A file that is not a database at all.
    broken = users_root / "bob" / "research_radar.db"
    broken.parent.mkdir(parents=True, exist_ok=True)
    broken.write_text("not a database", encoding="utf-8")

    result = collect_metrics(
        window_days=3650,
        accounts_db=tmp_path / "none.db",
        users_root=users_root,
        legacy_db=tmp_path / "none.db",
    )

    assert result["tenant_count"] == 2
    assert result["cost"]["total_usd"] == 0.01


def test_accounts_metrics_read_from_global_db(tmp_path):
    _make_accounts_db(
        tmp_path / "users.db",
        users=[("u1", "alice", NOW), ("u2", "bob", NOW)],
        sessions=[("t1", "u1", NOW), ("t2", "u1", NOW)],
    )

    result = collect_metrics(
        window_days=3650,
        accounts_db=tmp_path / "users.db",
        users_root=tmp_path / "no-users",
        legacy_db=tmp_path / "none.db",
    )

    assert result["users"]["total"] == 2
    # Two sessions belong to the same account: distinct users, not logins.
    assert result["users"]["active_in_window"] == 1


def test_window_filter_excludes_old_rows(tmp_path):
    users_root = tmp_path / "users"
    _make_product_db(
        users_root / "alice" / "research_radar.db",
        rows={
            "model_runs": [
                ("stage", "m1", 10, 5, 0.01, 100, "2000-01-01 00:00:00"),
                ("stage", "m1", 10, 5, 0.01, 100, NOW),
            ]
        },
    )

    result = collect_metrics(
        window_days=30,
        accounts_db=tmp_path / "none.db",
        users_root=users_root,
        legacy_db=tmp_path / "none.db",
    )

    # Only the recent row falls inside the window.
    assert result["cost"]["llm_calls"] == 1


def test_normalize_error_groups_variable_messages():
    """Ids and counts inside messages must not create one bucket per scan."""

    assert _normalize_error("1 routed pair(s) failed; see audit log.") == _normalize_error(
        "3 routed pair(s) failed; see audit log."
    )
    assert _normalize_error("ValueError: confirmed_claim_required") == "未确认 Claim 就扫描"
    assert _normalize_error("Client error '429 Too Many Requests'") == "LLM 限流 (429)"
    assert _normalize_error(None) == "unknown"


def test_empty_deployment_returns_safe_defaults(tmp_path):
    """A fresh install has no data; the dashboard must still render."""

    result = collect_metrics(
        window_days=30,
        accounts_db=tmp_path / "none.db",
        users_root=tmp_path / "none",
        legacy_db=tmp_path / "none.db",
    )

    assert result["tenant_count"] == 0
    assert result["cost"]["total_usd"] == 0.0
    assert result["health"]["success_rate"] is None
    assert result["health"]["avg_scan_seconds"] is None
    assert result["users"]["total"] == 0
