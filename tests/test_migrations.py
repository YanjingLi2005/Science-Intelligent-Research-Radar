"""Schema migration runner, cost estimation, and ModelRun traceability tests."""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import inspect, text

from radar.config import estimate_llm_cost_usd
from radar.db import create_db_engine, init_database, run_migrations
from radar.models import ModelRun, ResearchCase
from radar.schemas import ManuscriptUnderstandingOutput
from radar.services.manuscript_understanding_service import (
    ManuscriptUnderstandingService,
)


def test_init_database_applies_and_records_migrations(tmp_path):
    engine = create_db_engine(f"sqlite:///{tmp_path / 'fresh.db'}")
    init_database(engine)

    with engine.connect() as connection:
        versions = {
            row[0]
            for row in connection.execute(
                text("SELECT version FROM schema_migrations")
            )
        }
    assert versions == {1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12}

    # A second pass applies nothing and does not fail on duplicate records.
    assert run_migrations(engine) == []
    init_database(engine)
    engine.dispose()


def test_legacy_tables_gain_traceability_columns(tmp_path):
    engine = create_db_engine(f"sqlite:///{tmp_path / 'legacy.db'}")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE sources (id VARCHAR PRIMARY KEY)"))
        connection.execute(text("CREATE TABLE model_runs (id VARCHAR PRIMARY KEY)"))
        connection.execute(text("CREATE TABLE action_items (id VARCHAR PRIMARY KEY)"))

    init_database(engine)

    inspector = inspect(engine)
    source_columns = {column["name"] for column in inspector.get_columns("sources")}
    run_columns = {column["name"] for column in inspector.get_columns("model_runs")}
    action_columns = {
        column["name"] for column in inspector.get_columns("action_items")
    }
    assert {"venue", "publication_type", "pdf_url", "cited_by_count",
            "arxiv_primary_category", "fields_of_study_json", "ccf_rank"} <= source_columns
    assert {"case_id", "scan_run_id"} <= run_columns
    assert "advice_source" in action_columns
    index_names = {index["name"] for index in inspector.get_indexes("model_runs")}
    assert "ix_model_runs_case_id" in index_names
    assert "ix_model_runs_scan_run_id" in index_names

    # Re-running on the migrated database is a no-op.
    assert run_migrations(engine) == []
    engine.dispose()


def test_snapshot_migration_restores_abstract_identity(tmp_path):
    """Legacy source_snapshots with clobbered content_hash get their abstract
    identity restored and the full-text hash moved to its own column."""
    import hashlib

    abstract = "An abstract about radar research."
    full_text = "Full text body of the paper."
    engine = create_db_engine(f"sqlite:///{tmp_path / 'legacy-snapshots.db'}")
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE source_snapshots ("
                "id VARCHAR PRIMARY KEY, source_id VARCHAR, version_label VARCHAR,"
                " title VARCHAR, abstract TEXT, content_text TEXT, content_hash VARCHAR,"
                " event_time DATETIME, observed_at DATETIME)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO source_snapshots (id, source_id, version_label, title,"
                " abstract, content_text, content_hash) VALUES"
                " ('s1', 'src1', '2026-07-01:public-pdf', 'T', :a, :ft, :clobbered)"
            ),
            {
                "a": abstract,
                "ft": full_text,
                "clobbered": hashlib.sha256(full_text.encode()).hexdigest(),
            },
        )
        connection.execute(
            text(
                "INSERT INTO source_snapshots (id, source_id, version_label, title,"
                " abstract, content_text, content_hash) VALUES"
                " ('s2', 'src1', '2026-07-01', 'T', :a, :a, :abstract_hash)"
            ),
            {
                "a": abstract,
                "abstract_hash": hashlib.sha256(abstract.strip().encode()).hexdigest(),
            },
        )

    init_database(engine)

    with engine.connect() as connection:
        rows = {
            row[0]: (row[1], row[2])
            for row in connection.execute(
                text("SELECT id, content_hash, full_text_hash FROM source_snapshots")
            )
        }
    # Enriched legacy row: identity restored from the abstract, full-text hash
    # moved into its own column.
    assert rows["s1"][0] == hashlib.sha256(abstract.strip().encode()).hexdigest()
    assert rows["s1"][1] == hashlib.sha256(full_text.encode()).hexdigest()
    # Abstract-only row: unchanged identity, no full-text hash.
    assert rows["s2"][1] is None
    engine.dispose()


def test_estimate_llm_cost_usd_known_and_unknown_models():
    assert estimate_llm_cost_usd("deepseek-chat", 1_000_000, 500_000) == pytest.approx(
        0.82
    )
    assert estimate_llm_cost_usd("DeepSeek-Chat", 1_000_000, 500_000) == pytest.approx(
        0.82
    )
    assert estimate_llm_cost_usd("unknown-model", 1_000, 1_000) == 0.0
    assert estimate_llm_cost_usd(None, 0, 0) == 0.0


def _profile_payload(central_thesis: str) -> dict:
    return ManuscriptUnderstandingOutput(
        title="RadarNet",
        research_problem="Robust retrieval under domain shift",
        central_thesis=central_thesis,
        contributions=[],
        methods=[],
        datasets=[],
        evaluation_protocol=[],
        key_findings=[],
        limitations=[],
        terminology=[],
        watch_topics=[],
        claim_profiles=[],
    ).model_dump()


def _profile_run(case_id: str, central_thesis: str, created_at: datetime) -> ModelRun:
    return ModelRun(
        id=str(uuid4()),
        stage="manuscript_understanding",
        case_id=case_id,
        provider="test",
        model="test-model",
        prompt_hash="hash",
        schema_version="ManuscriptUnderstandingOutput.v1",
        input_refs_json=[],
        raw_response="{}",
        parsed_output_json=_profile_payload(central_thesis),
        validation_json={},
        created_at=created_at,
    )


def test_latest_profile_is_scoped_and_ordered_by_case(db_session_factory):
    now = datetime.now(timezone.utc)
    with db_session_factory() as session:
        session.add(ResearchCase(id="case-a", title="A", research_question="q"))
        session.add(ResearchCase(id="case-b", title="B", research_question="q"))
        session.commit()
    with db_session_factory() as session:
        session.add(_profile_run("case-a", "old thesis", now - timedelta(hours=2)))
        session.add(_profile_run("case-b", "other case thesis", now - timedelta(hours=1)))
        session.add(_profile_run("case-a", "new thesis", now))
        session.commit()

    loaded = ManuscriptUnderstandingService.latest_profile(
        "case-a", db_session_factory
    )
    assert loaded is not None
    assert loaded.central_thesis == "new thesis"

    loaded = ManuscriptUnderstandingService.latest_profile(
        "case-b", db_session_factory
    )
    assert loaded is not None
    assert loaded.central_thesis == "other case thesis"

    assert (
        ManuscriptUnderstandingService.latest_profile("case-c", db_session_factory)
        is None
    )
