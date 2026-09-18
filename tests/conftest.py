"""Shared isolated database and Golden Case fixtures."""

import os
from pathlib import Path

import pytest

# litellm (imported via paper-qa when test_paperqa2 is collected) calls
# load_dotenv() at import time, which merges the project .env into
# os.environ and breaks every Settings(_env_file=None)-based test. Gate it
# off before collection happens.
os.environ["LITELLM_MODE"] = "PROD"
# The /api routes require a bearer token once auth is enabled; the offline
# suite predates multi-user and hits them without one, so disable the gate.
# Auth tests re-enable it explicitly.
os.environ["RADAR_AUTH_DISABLED"] = "1"
# Keep the auto-scan scheduler out of the test suite.
os.environ["RADAR_SCHEDULER_DISABLED"] = "1"
from sqlalchemy.orm import sessionmaker

import radar.services.claim_service as claim_service_module
from radar.adapters.crossref import CrossrefIntegrityAdapter
from radar.adapters.unpaywall import UnpaywallAdapter
from radar.db import create_db_engine, init_database
from radar.services.case_service import CaseService


@pytest.fixture(autouse=True)
def crossref_offline_stub(monkeypatch):
    """Keep tests offline: default Crossref lookups to an empty message."""
    monkeypatch.setattr(CrossrefIntegrityAdapter, "_message", lambda self, doi: {})


@pytest.fixture(autouse=True)
def unpaywall_offline_stub(monkeypatch):
    """Keep tests offline: default Unpaywall lookups to a plain miss."""
    monkeypatch.setattr(UnpaywallAdapter, "_fetch_payload", lambda self, doi: {})


@pytest.fixture(autouse=True)
def deterministic_claim_extraction(monkeypatch):
    """Keep claim extraction on the heuristic path unless a test injects an LLM.

    The developer .env configures a real LLM; without this stub, default
    ClaimService instances created inside CaseService would attempt live
    network calls during unrelated tests.
    """
    monkeypatch.setattr(
        claim_service_module, "default_llm_client", lambda settings: None
    )


@pytest.fixture(autouse=True)
def nli_offline_stub(monkeypatch):
    """Keep tests offline: the NLI second opinion is inert by default (the
    real model downloads hundreds of MB on first use)."""

    class _FakeNLI:
        def check(self, claim, evidence):
            return None

    import radar.services.weekly_radar_service as wrs_module

    monkeypatch.setattr(wrs_module, "_DEFAULT_NLI_BUILDER", lambda: _FakeNLI())


@pytest.fixture(autouse=True)
def paperqa2_offline_stub(monkeypatch):
    """Keep tests offline: PaperQA2 adapter is inert unless injected.

    WeeklyRadarService now builds a real PaperQA2Adapter from settings by
    default; without this stub, uncertain/neutral assessments would make live
    litellm calls during unrelated tests.
    """
    import radar.services.weekly_radar_service as wrs_module

    class _FakePaperQA2:
        available = False

        def assess_claim(self, *args, **kwargs):
            return {
                "contradictions": [],
                "supporting": [],
                "verdict": "unavailable",
                "evidence_quality": "unknown",
            }

    monkeypatch.setattr(wrs_module, "_DEFAULT_PAPERQA2_BUILDER", lambda settings: _FakePaperQA2())


@pytest.fixture
def db_session_factory(tmp_path):
    engine = create_db_engine(f"sqlite:///{tmp_path / 'test.db'}")
    init_database(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    yield factory
    engine.dispose()


@pytest.fixture
def golden_dir() -> Path:
    return Path(__file__).parent / "fixtures" / "golden_case"


@pytest.fixture
def golden_case(db_session_factory, golden_dir):
    case_id = CaseService(db_session_factory).load_demo_case(golden_dir)
    return case_id

