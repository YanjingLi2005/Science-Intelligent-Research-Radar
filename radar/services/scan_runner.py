"""Background scan execution and the ScanRun status state machine.

States: ``running`` -> ``completed`` | ``failed`` | ``cancelled`` | ``interrupted``
with a cooperative detour ``running`` -> ``cancel_requested`` -> ``cancelled``.

The runner pre-creates the ScanRun row so the UI can poll progress from the
first second, executes ``WeeklyRadarService.run_auto`` on a daemon thread with
its own session, and guarantees the row always reaches a terminal state.
"""

import threading
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from radar.db import SessionLocal, get_session_factory, session_scope
from radar.models import ScanRun
from radar.services.weekly_radar_service import WeeklyRadarService
from radar.tenant import Tenant, current_tenant, tenant_context


ACTIVE_STATUSES = frozenset({"running", "cancel_requested"})
TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled", "interrupted"})
DEFAULT_STALE_MINUTES = 30


class ScanAlreadyRunningError(RuntimeError):
    """Raised when a case already has an active scan."""


# In-process guard complementing the DB-level active-scan check. The dict only
# tracks scans owned by live worker threads in this process; recovery uses it
# to avoid interrupting scans that are genuinely alive.
_lock = threading.Lock()
_active_by_case: dict[str, str] = {}  # case_id -> scan_id
_threads: dict[str, threading.Thread] = {}


def start(
    case_id: str,
    *,
    max_results: int = 32,
    analysis_limit: int = 3,
    session_factory: sessionmaker[Session] | None = None,
    service_factory: Callable[[sessionmaker[Session]], Any] = WeeklyRadarService,
    mode: str = "auto_public_paper_radar",
    query_json: dict | None = None,
    run_func: Callable[..., Any] | None = None,
    run_kwargs: dict | None = None,
) -> str:
    """Pre-create a running ScanRun and execute it on a background thread.

    Raises ScanAlreadyRunningError when the case already has an active scan,
    either owned by this process or recorded in the database.

    The active tenant (and its per-user factory) is captured so the daemon
    thread keeps using that account's database and LLM settings.

    ``mode`` / ``query_json`` / ``run_func`` / ``run_kwargs`` let callers run
    other bounded research workflows (e.g. deep research) through the same
    active-scan guard and progress state machine.
    """

    factory = session_factory or get_session_factory()
    tenant = current_tenant()
    run_func = run_func or _default_run_auto
    run_kwargs = dict(run_kwargs or {})
    if not run_kwargs and mode == "auto_public_paper_radar":
        run_kwargs = {
            "max_results": max_results,
            "analysis_limit": analysis_limit,
        }
    if query_json is None:
        query_json = {
            "max_results": max_results,
            "analysis_limit": analysis_limit,
        }
    global _threads
    with _lock:
        # Purge finished worker threads so a long-lived process does not
        # accumulate one dead Thread object per scan.
        _threads = {sid: t for sid, t in _threads.items() if t.is_alive()}
        stale_scan_id: str | None = None
        if case_id in _active_by_case:
            guarded_scan_id = _active_by_case[case_id]
            owner = _threads.get(guarded_scan_id)
            if owner is not None and owner.is_alive():
                # A live worker thread in this process genuinely owns the case.
                raise ScanAlreadyRunningError(
                    f"case already has a running scan: {case_id}"
                )
            # The guard entry outlived its worker thread (the thread was torn
            # down without reaching its finally block). Release the guard and
            # heal the orphaned row below, so the case does not stay blocked
            # until a process restart.
            _active_by_case.pop(case_id, None)
            stale_scan_id = guarded_scan_id
        with session_scope(factory) as session:
            if stale_scan_id is not None:
                orphan = session.get(ScanRun, stale_scan_id)
                if orphan is not None and orphan.status in ACTIVE_STATUSES:
                    # Safe to finalize immediately — the owning thread lived in
                    # this process and is verifiably dead, so no staleness
                    # window is needed.
                    orphan.status = "interrupted"
                    orphan.error_message = orphan.error_message or (
                        "Scan worker thread died without finalizing; "
                        "recovered when a new scan was requested."
                    )
                    orphan.finished_at = datetime.now(timezone.utc)
                    # The factory sets autoflush=False: flush explicitly so
                    # the active-scan query below sees the healed status.
                    session.flush()
            active = session.scalar(
                select(ScanRun.id).where(
                    ScanRun.case_id == case_id,
                    ScanRun.status.in_(sorted(ACTIVE_STATUSES)),
                )
            )
            if active is not None:
                raise ScanAlreadyRunningError(
                    f"case already has an active scan in the database: {case_id}"
                )
            scan = ScanRun(
                id=str(uuid4()),
                case_id=case_id,
                mode=mode,
                status="running",
                started_at=datetime.now(timezone.utc),
                query_json=query_json,
                stats_json={
                    "progress": {"value": 0.0, "message": "任务已排队，正在启动…"}
                },
            )
            session.add(scan)
            session.flush()
            scan_id = scan.id
        _active_by_case[case_id] = scan_id

    thread = threading.Thread(
        target=_run_worker,
        args=(
            case_id,
            scan_id,
            max_results,
            analysis_limit,
            factory,
            tenant,
            service_factory,
            run_func,
            run_kwargs,
        ),
        name=f"scan-runner-{scan_id[:8]}",
        daemon=True,
    )
    with _lock:
        _threads[scan_id] = thread
    thread.start()
    return scan_id


def start_deep_research(
    case_id: str,
    *,
    question: str = "",
    depth: int = 1,
    max_sources: int = 12,
    max_subqueries: int = 5,
    session_factory: sessionmaker[Session] | None = None,
    service_factory: Callable[[sessionmaker[Session]], Any] | None = None,
) -> str:
    """Launch a bounded deep-research run through the shared runner."""
    from radar.services.deep_research_service import DeepResearchService

    factory = session_factory or get_session_factory()
    service = service_factory or DeepResearchService
    query_payload = {
        "question": question,
        "depth": depth,
        "max_sources": max_sources,
        "max_subqueries": max_subqueries,
    }
    return start(
        case_id,
        session_factory=factory,
        service_factory=service,
        mode="deep_research",
        query_json=query_payload,
        run_func=lambda svc, **kwargs: svc.run(**kwargs),
        run_kwargs=query_payload,
    )


def request_cancel(
    scan_id: str, *, session_factory: sessionmaker[Session] | None = None
) -> bool:
    """Flag an active scan for cooperative cancellation; return False if done."""

    with session_scope(session_factory) as session:
        scan = session.get(ScanRun, scan_id)
        if scan is None:
            raise LookupError(f"scan not found: {scan_id}")
        if scan.status not in ACTIVE_STATUSES:
            return False
        scan.status = "cancel_requested"
        scan.stats_json = {**(scan.stats_json or {}), "cancel_requested": True}
    return True


def get_active_scan(
    case_id: str, *, session_factory: sessionmaker[Session] | None = None
) -> ScanRun | None:
    """Return the case's active ScanRun (detached), or None."""

    with session_scope(session_factory) as session:
        scan = session.scalar(
            select(ScanRun)
            .where(
                ScanRun.case_id == case_id,
                ScanRun.status.in_(sorted(ACTIVE_STATUSES)),
            )
            .order_by(ScanRun.created_at.desc())
        )
        if scan is not None:
            session.expunge(scan)
        return scan


def recover_interrupted_scans(
    *,
    session_factory: sessionmaker[Session] | None = None,
    stale_minutes: int = DEFAULT_STALE_MINUTES,
) -> int:
    """Mark active scans without a heartbeat for ``stale_minutes`` as interrupted.

    Scans owned by live worker threads in this process are never touched.
    """

    cutoff = datetime.now(timezone.utc) - timedelta(minutes=stale_minutes)
    recovered = 0
    with session_scope(session_factory) as session:
        stale = session.scalars(
            select(ScanRun).where(
                ScanRun.status.in_(sorted(ACTIVE_STATUSES)),
                ScanRun.updated_at < cutoff,
            )
        ).all()
        for scan in stale:
            with _lock:
                owner = _threads.get(scan.id)
                if (
                    scan.id in _active_by_case.values()
                    and owner is not None
                    and owner.is_alive()
                ):
                    # A live worker thread in this process still owns the row.
                    continue
                # No live owner: also drop any stale guard entry pointing at
                # this row so the case unblocks together with it.
                for held_case, held_scan in list(_active_by_case.items()):
                    if held_scan == scan.id:
                        _active_by_case.pop(held_case, None)
                scan.status = "interrupted"
                scan.error_message = scan.error_message or (
                    "Scan showed no progress for over "
                    f"{stale_minutes} minutes; marked interrupted."
                )
                scan.finished_at = datetime.now(timezone.utc)
                recovered += 1
    return recovered


def wait_for_scan(scan_id: str, timeout: float | None = None) -> bool:
    """Join the worker thread of a scan; return True when it has finished."""

    with _lock:
        thread = _threads.get(scan_id)
    if thread is None:
        return False
    thread.join(timeout)
    return not thread.is_alive()


def _default_run_auto(service, **kwargs):
    """Default worker callable for the weekly radar scan."""
    return service.run_auto(**kwargs)


def _run_worker(
    case_id: str,
    scan_id: str,
    max_results: int,
    analysis_limit: int,
    session_factory: sessionmaker[Session],
    tenant: Tenant | None,
    service_factory: Callable[[sessionmaker[Session]], Any],
    run_func: Callable[..., Any],
    run_kwargs: dict,
) -> None:
    """Thread entry point; never lets the ScanRun stay in an active state."""

    try:
        with tenant_context(tenant):
            service = service_factory(session_factory)

            def progress_callback(value: float, message: str) -> None:
                _write_progress(session_factory, scan_id, value, message)

            def cancel_check() -> bool:
                return _cancel_requested(session_factory, scan_id)

            run_func(
                service,
                case_id=case_id,
                scan_id=scan_id,
                progress_callback=progress_callback,
                cancel_check=cancel_check,
                **run_kwargs,
            )
            _finalize_progress(session_factory, scan_id)
    except Exception as exc:  # noqa: BLE001 - the scan row must not outlive us
        _mark_failed(session_factory, scan_id, exc)
    finally:
        with _lock:
            _active_by_case.pop(case_id, None)


def _write_progress(
    session_factory: sessionmaker[Session],
    scan_id: str,
    value: float,
    message: str,
) -> None:
    """Persist UI-polled progress into stats_json with a short-lived session."""

    with session_scope(session_factory) as session:
        scan = session.get(ScanRun, scan_id)
        if scan is None:
            return
        scan.stats_json = {
            **(scan.stats_json or {}),
            "progress": {
                "value": max(0.0, min(float(value), 1.0)),
                "message": message,
            },
        }


def _cancel_requested(
    session_factory: sessionmaker[Session], scan_id: str
) -> bool:
    """Read the cancel flag fresh from the database on every poll."""

    with session_factory() as session:
        scan = session.get(ScanRun, scan_id)
        if scan is None:
            return False
        return scan.status == "cancel_requested" or bool(
            (scan.stats_json or {}).get("cancel_requested")
        )


def _finalize_progress(
    session_factory: sessionmaker[Session], scan_id: str
) -> None:
    """Stamp a terminal progress message once run_auto returns cleanly."""

    with session_scope(session_factory) as session:
        scan = session.get(ScanRun, scan_id)
        if scan is None or scan.status in ACTIVE_STATUSES:
            return
        progress = dict((scan.stats_json or {}).get("progress") or {})
        if scan.status == "completed":
            progress.update({
                "value": 1.0,
                "message": "深度调研完成。" if scan.mode == "deep_research" else "扫描完成。",
            })
        elif scan.status == "cancelled":
            progress["message"] = (
                "深度调研已取消，取消前完成的中途结果已保留。"
                if scan.mode == "deep_research"
                else "扫描已取消，取消前完成的中途结果已保留。"
            )
        else:
            return
        scan.stats_json = {**(scan.stats_json or {}), "progress": progress}


def _mark_failed(
    session_factory: sessionmaker[Session], scan_id: str, exc: Exception
) -> None:
    """Record any worker-thread exception on the ScanRun instead of dying quietly."""

    with session_scope(session_factory) as session:
        scan = session.get(ScanRun, scan_id)
        if scan is None:
            return
        if scan.status in ACTIVE_STATUSES:
            scan.status = "failed"
            scan.finished_at = datetime.now(timezone.utc)
            scan.error_message = f"{type(exc).__name__}: {exc}"
        progress = dict((scan.stats_json or {}).get("progress") or {})
        progress["message"] = f"扫描失败：{exc}"
        scan.stats_json = {**(scan.stats_json or {}), "progress": progress}
