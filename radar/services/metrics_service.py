"""Cross-tenant product metrics for the internal monitoring dashboard.

Multi-user deployments shard business data per account (``data/users/<name>/
research_radar.db``) while accounts themselves live in one global
``data/users.db``. A product-wide dashboard therefore has to fan out over every
tenant database and fold the per-tenant numbers together.

Design notes:

* Read-only ``sqlite3`` connections are used instead of the SQLAlchemy session
  layer. Aggregation touches many databases that are not the request's own
  tenant, so binding an ORM engine per tenant would both fight the tenant
  ContextVar and keep those engines alive for the process. ``mode=ro`` also
  makes it impossible for a reporting query to mutate product data.
* Every per-tenant read is individually guarded: one missing table or corrupt
  file degrades that tenant to zeros instead of failing the whole dashboard.
  Older databases predating a table must not break monitoring.
* All inputs are injectable paths so tests can point at temporary fixtures.
"""

import sqlite3
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from radar.config import PROJECT_ROOT


DEFAULT_WINDOW_DAYS = 30
# Audit events that represent something going wrong for the user, as opposed
# to normal lifecycle events. Drives the "problems users hit" panel.
FAILURE_EVENT_TYPES = (
    "weekly_scan_failed",
    "weekly_pair_failed",
    "impact_blocked",
)
TOP_N = 5


def _accounts_db_path() -> Path:
    return PROJECT_ROOT / "data" / "users.db"


def _users_root() -> Path:
    return PROJECT_ROOT / "data" / "users"


def _legacy_db_path() -> Path:
    """Single-tenant database used before per-user sharding (still in use in
    local development and in any deployment without accounts)."""

    return PROJECT_ROOT / "data" / "research_radar.db"


def _connect_ro(path: Path) -> sqlite3.Connection | None:
    """Open a read-only connection, or None when the file is unusable."""

    if not path.exists():
        return None
    try:
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        return connection
    except sqlite3.Error:
        return None


def _rows(connection: sqlite3.Connection, sql: str, params: tuple = ()) -> list:
    """Run one aggregate query, returning [] if the table is absent."""

    try:
        return list(connection.execute(sql, params))
    except sqlite3.Error:
        # A database created before this table existed must not break the
        # whole dashboard; that tenant simply contributes nothing here.
        return []


def _cutoff(window_days: int) -> str:
    """Window boundary formatted to match the stored ``created_at`` text."""

    moment = datetime.now(timezone.utc) - timedelta(days=window_days)
    return moment.strftime("%Y-%m-%d %H:%M:%S")


def _tenant_db_paths(users_root: Path, legacy_db: Path) -> list[Path]:
    """Every product database to aggregate: one per account, plus the legacy
    single-tenant file when it exists."""

    paths: list[Path] = []
    if users_root.exists():
        for entry in sorted(users_root.iterdir()):
            candidate = entry / "research_radar.db"
            if entry.is_dir() and candidate.exists():
                paths.append(candidate)
    if legacy_db.exists():
        paths.append(legacy_db)
    return paths


def _collect_accounts(accounts_db: Path, cutoff: str) -> dict[str, int]:
    """Registration and login counts from the global accounts database.

    ``active`` counts accounts that logged in during the window: sessions are
    created at login, so this measures returning logins rather than true
    in-app daily activity. Real DAU needs a request-level heartbeat, which is
    not instrumented yet.
    """

    result = {"total": 0, "new_in_window": 0, "active_in_window": 0}
    connection = _connect_ro(accounts_db)
    if connection is None:
        return result
    try:
        totals = _rows(connection, "SELECT COUNT(*) AS n FROM users")
        if totals:
            result["total"] = int(totals[0]["n"])
        recent = _rows(
            connection, "SELECT COUNT(*) AS n FROM users WHERE created_at >= ?", (cutoff,)
        )
        if recent:
            result["new_in_window"] = int(recent[0]["n"])
        active = _rows(
            connection,
            "SELECT COUNT(DISTINCT user_id) AS n FROM sessions WHERE created_at >= ?",
            (cutoff,),
        )
        if active:
            result["active_in_window"] = int(active[0]["n"])
    finally:
        connection.close()
    return result


def _collect_tenant(path: Path, cutoff: str) -> dict[str, Any]:
    """Aggregate one product database into raw counters."""

    blank: dict[str, Any] = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cost_usd": 0.0,
        "llm_calls": 0,
        "latency_ms_total": 0,
        "cost_by_stage": Counter(),
        "tokens_by_model": Counter(),
        "scans_by_status": Counter(),
        "scan_duration_total": 0.0,
        "scan_duration_count": 0,
        "failure_events": Counter(),
        "scan_errors": Counter(),
    }
    connection = _connect_ro(path)
    if connection is None:
        return blank
    try:
        for row in _rows(
            connection,
            "SELECT stage, model, COUNT(*) AS calls, "
            "SUM(input_tokens) AS inp, SUM(output_tokens) AS out, "
            "SUM(estimated_cost) AS cost, SUM(latency_ms) AS latency "
            "FROM model_runs WHERE created_at >= ? GROUP BY stage, model",
            (cutoff,),
        ):
            inp = int(row["inp"] or 0)
            out = int(row["out"] or 0)
            cost = float(row["cost"] or 0.0)
            blank["input_tokens"] += inp
            blank["output_tokens"] += out
            blank["cost_usd"] += cost
            blank["llm_calls"] += int(row["calls"] or 0)
            blank["latency_ms_total"] += int(row["latency"] or 0)
            blank["cost_by_stage"][row["stage"] or "unknown"] += cost
            blank["tokens_by_model"][row["model"] or "unknown"] += inp + out

        for row in _rows(
            connection,
            "SELECT status, COUNT(*) AS n FROM scan_runs WHERE created_at >= ? "
            "GROUP BY status",
            (cutoff,),
        ):
            blank["scans_by_status"][row["status"] or "unknown"] += int(row["n"] or 0)

        for row in _rows(
            connection,
            "SELECT SUM((julianday(finished_at) - julianday(started_at)) * 86400) "
            "AS total, COUNT(*) AS n FROM scan_runs "
            "WHERE created_at >= ? AND finished_at IS NOT NULL "
            "AND started_at IS NOT NULL",
            (cutoff,),
        ):
            blank["scan_duration_total"] += float(row["total"] or 0.0)
            blank["scan_duration_count"] += int(row["n"] or 0)

        placeholders = ",".join("?" for _ in FAILURE_EVENT_TYPES)
        for row in _rows(
            connection,
            f"SELECT event_type, COUNT(*) AS n FROM audit_events "
            f"WHERE created_at >= ? AND event_type IN ({placeholders}) "
            "GROUP BY event_type",
            (cutoff, *FAILURE_EVENT_TYPES),
        ):
            blank["failure_events"][row["event_type"]] += int(row["n"] or 0)

        for row in _rows(
            connection,
            "SELECT error_message FROM scan_runs "
            "WHERE created_at >= ? AND error_message IS NOT NULL",
            (cutoff,),
        ):
            blank["scan_errors"][_normalize_error(row["error_message"])] += 1
    finally:
        connection.close()
    return blank


def _normalize_error(message: str | None) -> str:
    """Collapse an error message to a groupable label.

    Raw messages embed ids and urls, so grouping on them verbatim would
    produce one bucket per occurrence and hide which failures are common.
    """

    text = " ".join((message or "").split())
    if not text:
        return "unknown"
    if "429" in text or "Too Many Requests" in text:
        return "LLM 限流 (429)"
    if "llm_not_configured" in text:
        return "LLM 未配置"
    if "confirmed_claim_required" in text:
        return "未确认 Claim 就扫描"
    if "already has a running scan" in text or "already has an active scan" in text:
        return "扫描并发锁阻塞"
    if "no progress" in text or "interrupted" in text.lower():
        return "扫描中断 (无心跳)"
    if "routed pair" in text:
        # The count varies per scan ("1 routed pair(s) failed", "3 routed..."),
        # so strip it or every scan lands in its own bucket.
        return "部分文献比对失败"
    if "cancelled by user" in text.lower():
        return "用户主动取消"
    head = text.split(":", 1)[0]
    return head[:60] if head else text[:60]


def collect_metrics(
    *,
    window_days: int = DEFAULT_WINDOW_DAYS,
    accounts_db: Path | None = None,
    users_root: Path | None = None,
    legacy_db: Path | None = None,
) -> dict[str, Any]:
    """Aggregate product metrics across every tenant database.

    Returns plain JSON-ready data covering the four dashboard panels: user
    activity, token spend, problems users hit, and system health.
    """

    accounts_db = accounts_db or _accounts_db_path()
    users_root = users_root or _users_root()
    legacy_db = legacy_db or _legacy_db_path()
    cutoff = _cutoff(window_days)

    paths = _tenant_db_paths(users_root, legacy_db)
    totals = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cost_usd": 0.0,
        "llm_calls": 0,
        "latency_ms_total": 0,
        "scan_duration_total": 0.0,
        "scan_duration_count": 0,
    }
    cost_by_stage: Counter = Counter()
    tokens_by_model: Counter = Counter()
    scans_by_status: Counter = Counter()
    failure_events: Counter = Counter()
    scan_errors: Counter = Counter()

    for path in paths:
        tenant = _collect_tenant(path, cutoff)
        for key in totals:
            totals[key] += tenant[key]
        cost_by_stage.update(tenant["cost_by_stage"])
        tokens_by_model.update(tenant["tokens_by_model"])
        scans_by_status.update(tenant["scans_by_status"])
        failure_events.update(tenant["failure_events"])
        scan_errors.update(tenant["scan_errors"])

    total_scans = sum(scans_by_status.values())
    completed = scans_by_status.get("completed", 0)
    failed = scans_by_status.get("failed", 0) + scans_by_status.get("interrupted", 0)
    # Cancelled scans are user intent, not a defect: they are excluded from the
    # denominator so a cancel-heavy week cannot look like a reliability drop.
    decided = completed + failed

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "window_days": window_days,
        "tenant_count": len(paths),
        "users": _collect_accounts(accounts_db, cutoff),
        "cost": {
            "total_usd": round(totals["cost_usd"], 4),
            "input_tokens": totals["input_tokens"],
            "output_tokens": totals["output_tokens"],
            "total_tokens": totals["input_tokens"] + totals["output_tokens"],
            "llm_calls": totals["llm_calls"],
            "avg_usd_per_scan": (
                round(totals["cost_usd"] / total_scans, 4) if total_scans else 0.0
            ),
            "by_stage": [
                {"stage": stage, "usd": round(value, 4)}
                for stage, value in cost_by_stage.most_common(TOP_N)
            ],
            "by_model": [
                {"model": model, "tokens": value}
                for model, value in tokens_by_model.most_common(TOP_N)
            ],
        },
        "problems": {
            "failed_scans": failed,
            "top_errors": [
                {"reason": reason, "count": count}
                for reason, count in scan_errors.most_common(TOP_N)
            ],
            "failure_events": [
                {"event_type": event, "count": count}
                for event, count in failure_events.most_common(TOP_N)
            ],
        },
        "health": {
            "total_scans": total_scans,
            "scans_by_status": dict(scans_by_status),
            "success_rate": round(completed / decided, 4) if decided else None,
            "avg_scan_seconds": (
                round(totals["scan_duration_total"] / totals["scan_duration_count"], 1)
                if totals["scan_duration_count"]
                else None
            ),
            "avg_llm_latency_ms": (
                round(totals["latency_ms_total"] / totals["llm_calls"])
                if totals["llm_calls"]
                else None
            ),
        },
    }
