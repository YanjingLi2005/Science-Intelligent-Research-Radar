"""Background scan runner: state machine, reentrancy, cancel, recovery."""

import threading
import time
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from radar.config import Settings
from radar.models import ScanRun
from radar.schemas import (
    EvidenceSpan,
    ImpactAssessmentOutput,
    IncomingResult,
    SourceRecord,
)
from radar.services import scan_runner
from radar.services.weekly_radar_service import WeeklyRadarService


INCOMING_QUOTE = (
    "On the DomainQA unseen-domain split, NewRAG reaches 70.1 exact match while "
    "RadarNet reaches 64.0 using the same BM25 baseline."
)
OWN_QUOTE = (
    "On DomainQA, RadarNet improves exact match from 61.2% to 68.7% over BM25 "
    "under unseen-domain evaluation."
)


class OnePaperSearch:
    def search(self, case_id, watch_query):
        return [
            SourceRecord(
                external_id="arxiv:test-runner-1",
                title="A Matched Re-evaluation of RadarNet",
                authors=["Independent Lab"],
                abstract=INCOMING_QUOTE,
                url="https://arxiv.org/abs/test-runner-1",
                published_at="2026-07-20T00:00:00Z",
                arxiv_id="test-runner-1",
            )
        ]


class ScriptedLLM:
    def generate_structured(self, *, stage, prompt, response_model, max_tokens=None):
        if stage == "incoming_result":
            return IncomingResult(
                task="open-domain QA",
                dataset="DomainQA",
                split="unseen-domain",
                metric="exact match",
                comparator="BM25",
                scope="RadarNet",
                direction="challenges",
            )
        if stage == "impact_assessment":
            return ImpactAssessmentOutput(
                stance="challenges",
                impact_mode="boundary_condition",
                comparability="compatible",
                condition_differences=[],
                evidence_own=EvidenceSpan(
                    quote=OWN_QUOTE, locator="sec:main-results:p:1"
                ),
                evidence_new=EvidenceSpan(
                    quote=INCOMING_QUOTE, locator="paragraph:1"
                ),
                change_depth=3,
                suggested_action="narrow_claim",
                uncertainty_sources=[],
            )
        raise AssertionError(f"unexpected stage: {stage}")


def _settings():
    return Settings(
        _env_file=None,
        llm_provider="deepseek",
        llm_api_key="sk-test-key-000000000000",
        llm_model="deepseek-v4-pro",
        llm_base_url="https://api.deepseek.com",
    )


class StubScanService:
    """Slow run_auto stand-in driving the runner-managed ScanRun row."""

    def __init__(self, session_factory, *, steps=3, step_delay=0.01, gate=None):
        self.session_factory = session_factory
        self.steps = steps
        self.step_delay = step_delay
        self.gate = gate
        self.completed_steps = 0

    def run_auto(
        self,
        case_id,
        *,
        max_results,
        analysis_limit,
        progress_callback=None,
        scan_id=None,
        cancel_check=None,
    ):
        if self.gate is not None:
            self.gate.wait(timeout=10)
        for step in range(1, self.steps + 1):
            if cancel_check is not None and cancel_check():
                self._finish(scan_id, "cancelled")
                return scan_id
            time.sleep(self.step_delay)
            self.completed_steps = step
            if progress_callback is not None:
                progress_callback(step / self.steps, f"step {step}/{self.steps}")
        self._finish(scan_id, "completed")
        return scan_id

    def _finish(self, scan_id, status):
        with self.session_factory() as session:
            scan = session.get(ScanRun, scan_id)
            scan.status = status
            scan.finished_at = datetime.now(timezone.utc)
            session.commit()


class ExplodingScanService(StubScanService):
    def run_auto(self, case_id, **kwargs):
        raise RuntimeError("boom")


@pytest.fixture(autouse=True)
def _clean_runner_state():
    yield
    with scan_runner._lock:
        scan_runner._active_by_case.clear()
        scan_runner._threads.clear()


def _start_stub(db_session_factory, case_id, service):
    return scan_runner.start(
        case_id,
        max_results=5,
        analysis_limit=1,
        session_factory=db_session_factory,
        service_factory=lambda session_factory: service,
    )


def test_background_scan_completes_and_writes_progress(db_session_factory, golden_case):
    service = StubScanService(db_session_factory, steps=3)

    scan_id = _start_stub(db_session_factory, golden_case, service)

    with db_session_factory() as session:
        scan = session.get(ScanRun, scan_id)
        # The row exists and is active from the moment start() returns.
        assert scan.status == "running"
        assert scan.started_at is not None
    assert scan_runner.wait_for_scan(scan_id, timeout=10)
    with db_session_factory() as session:
        scan = session.get(ScanRun, scan_id)
        assert scan.status == "completed"
        assert scan.finished_at is not None
        assert scan.stats_json["progress"] == {"value": 1.0, "message": "扫描完成。"}


def test_duplicate_start_rejected_by_lock_and_database(
    db_session_factory, golden_case
):
    gate = threading.Event()
    service = StubScanService(db_session_factory, steps=1, gate=gate)
    scan_id = _start_stub(db_session_factory, golden_case, service)
    try:
        # In-process lock: a second start for the same case fails fast.
        with pytest.raises(scan_runner.ScanAlreadyRunningError):
            _start_stub(db_session_factory, golden_case, service)
    finally:
        gate.set()
    assert scan_runner.wait_for_scan(scan_id, timeout=10)

    # Database guard: an active row from elsewhere also blocks start.
    with db_session_factory() as session:
        session.add(
            ScanRun(
                id=str(uuid4()),
                case_id=golden_case,
                mode="auto_public_paper_radar",
                status="running",
                started_at=datetime.now(timezone.utc),
                stats_json={},
            )
        )
        session.commit()
    with pytest.raises(scan_runner.ScanAlreadyRunningError):
        _start_stub(db_session_factory, golden_case, service)


def test_request_cancel_stops_scan_at_stage_boundary(db_session_factory, golden_case):
    service = StubScanService(db_session_factory, steps=200, step_delay=0.01)
    scan_id = _start_stub(db_session_factory, golden_case, service)

    assert scan_runner.request_cancel(scan_id, session_factory=db_session_factory) is True
    with db_session_factory() as session:
        scan = session.get(ScanRun, scan_id)
        assert scan.status == "cancel_requested"
        assert scan.stats_json["cancel_requested"] is True

    assert scan_runner.wait_for_scan(scan_id, timeout=10)
    with db_session_factory() as session:
        scan = session.get(ScanRun, scan_id)
        assert scan.status == "cancelled"
        assert scan.stats_json["progress"]["message"] == (
            "扫描已取消，取消前完成的中途结果已保留。"
        )
    # The scan stopped at a stage boundary instead of running to the end.
    assert 0 < service.completed_steps < 200


def test_recover_marks_stale_running_scan_interrupted(db_session_factory, golden_case):
    stale_id, fresh_id = str(uuid4()), str(uuid4())
    with db_session_factory() as session:
        session.add(
            ScanRun(
                id=stale_id,
                case_id=golden_case,
                mode="auto_public_paper_radar",
                status="running",
                started_at=datetime.now(timezone.utc) - timedelta(minutes=120),
                updated_at=datetime.now(timezone.utc) - timedelta(minutes=90),
                stats_json={},
            )
        )
        session.add(
            ScanRun(
                id=fresh_id,
                case_id=golden_case,
                mode="auto_public_paper_radar",
                status="running",
                started_at=datetime.now(timezone.utc),
                stats_json={},
            )
        )
        session.commit()

    assert scan_runner.recover_interrupted_scans(
        session_factory=db_session_factory, stale_minutes=30
    ) == 1

    with db_session_factory() as session:
        stale = session.get(ScanRun, stale_id)
        fresh = session.get(ScanRun, fresh_id)
        assert stale.status == "interrupted"
        assert "no progress" in stale.error_message
        assert stale.finished_at is not None
        assert fresh.status == "running"
    # A second sweep finds nothing left to recover.
    assert scan_runner.recover_interrupted_scans(
        session_factory=db_session_factory, stale_minutes=30
    ) == 0


def test_worker_exception_marks_scan_failed(db_session_factory, golden_case):
    scan_id = _start_stub(
        db_session_factory, golden_case, ExplodingScanService(db_session_factory)
    )

    assert scan_runner.wait_for_scan(scan_id, timeout=10)
    with db_session_factory() as session:
        scan = session.get(ScanRun, scan_id)
        assert scan.status == "failed"
        assert "boom" in scan.error_message
        assert "扫描失败" in scan.stats_json["progress"]["message"]

    # The per-case lock was released, so a retry can start immediately.
    retry_id = _start_stub(
        db_session_factory, golden_case, StubScanService(db_session_factory)
    )
    assert scan_runner.wait_for_scan(retry_id, timeout=10)
    with db_session_factory() as session:
        assert session.get(ScanRun, retry_id).status == "completed"


def test_run_auto_cancel_check_marks_real_scan_cancelled(
    db_session_factory, golden_case
):
    service = WeeklyRadarService(
        db_session_factory,
        search_adapter=OnePaperSearch(),
        llm_client=ScriptedLLM(),
        settings=_settings(),
    )

    scan_id = service.run_auto(
        golden_case,
        max_results=5,
        analysis_limit=1,
        cancel_check=lambda: True,
    )

    with db_session_factory() as session:
        scan = session.get(ScanRun, scan_id)
        assert scan.status == "cancelled"
        assert scan.stats_json["cancelled"] is True
        assert scan.error_message == "Scan cancelled by user."


def test_run_auto_claims_precreated_scan_row(db_session_factory, golden_case):
    """The runner-owned row is reused instead of inserting a second ScanRun."""

    precreated_id = str(uuid4())
    with db_session_factory() as session:
        session.add(
            ScanRun(
                id=precreated_id,
                case_id=golden_case,
                mode="auto_public_paper_radar",
                status="running",
                started_at=datetime.now(timezone.utc),
                stats_json={"progress": {"value": 0.0, "message": "queued"}},
            )
        )
        session.commit()

    service = WeeklyRadarService(
        db_session_factory,
        search_adapter=OnePaperSearch(),
        llm_client=ScriptedLLM(),
        settings=_settings(),
    )
    scan_id = service.run_auto(
        golden_case, max_results=5, analysis_limit=1, scan_id=precreated_id
    )

    assert scan_id == precreated_id
    with db_session_factory() as session:
        scan = session.get(ScanRun, precreated_id)
        assert scan.status == "completed"
        # Final stats merged with the runner-written progress marker.
        assert scan.stats_json["progress"]["value"] == 0.0
        assert scan.stats_json["scanned_papers"] == 1
        assert scan.query_json["max_results"] == 5


def test_start_releases_stale_guard_from_dead_worker(db_session_factory, golden_case):
    """A guard entry whose worker thread is gone must not block new scans.

    Regression test: a worker torn down without reaching its finally block
    used to leave _active_by_case populated forever, so every new start()
    raised ScanAlreadyRunningError until a process restart.
    """

    orphan_id = str(uuid4())
    with db_session_factory() as session:
        session.add(
            ScanRun(
                id=orphan_id,
                case_id=golden_case,
                mode="auto_public_paper_radar",
                status="running",
                started_at=datetime.now(timezone.utc),
                stats_json={},
            )
        )
        session.commit()
    # Simulate the leak: guard entry present, no live thread registered.
    with scan_runner._lock:
        scan_runner._active_by_case[golden_case] = orphan_id
    try:
        retry_id = _start_stub(
            db_session_factory, golden_case, StubScanService(db_session_factory, steps=1)
        )
        assert scan_runner.wait_for_scan(retry_id, timeout=10)
        with db_session_factory() as session:
            orphan = session.get(ScanRun, orphan_id)
            retry = session.get(ScanRun, retry_id)
            # The orphaned row is finalized immediately, without a staleness wait.
            assert orphan.status == "interrupted"
            assert "died without finalizing" in orphan.error_message
            assert orphan.finished_at is not None
            assert retry.status == "completed"
    finally:
        with scan_runner._lock:
            scan_runner._active_by_case.pop(golden_case, None)


def test_start_still_blocks_while_worker_thread_alive(db_session_factory, golden_case):
    """A guard entry backed by a live thread keeps rejecting concurrent scans."""

    fake_scan_id = str(uuid4())
    release = threading.Event()
    worker = threading.Thread(target=release.wait, daemon=True)
    worker.start()
    with scan_runner._lock:
        scan_runner._active_by_case[golden_case] = fake_scan_id
        scan_runner._threads[fake_scan_id] = worker
    try:
        with pytest.raises(scan_runner.ScanAlreadyRunningError):
            _start_stub(
                db_session_factory,
                golden_case,
                StubScanService(db_session_factory, steps=1),
            )
    finally:
        release.set()
        worker.join(timeout=5)
        with scan_runner._lock:
            scan_runner._active_by_case.pop(golden_case, None)
            scan_runner._threads.pop(fake_scan_id, None)
