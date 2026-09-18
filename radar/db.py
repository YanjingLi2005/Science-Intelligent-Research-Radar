"""SQLAlchemy engine and session primitives."""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import threading

from sqlalchemy import Engine, create_engine, inspect, text
from sqlalchemy import event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from radar.config import get_settings


class Base(DeclarativeBase):
    """Declarative base for ORM models introduced in later milestones."""


def _prepare_sqlite_directory(database_url: str) -> None:
    """Create the parent directory for a file-backed SQLite database."""
    url = make_url(database_url)
    if url.get_backend_name() != "sqlite" or not url.database:
        return
    if url.database == ":memory:":
        return

    Path(url.database).expanduser().parent.mkdir(parents=True, exist_ok=True)


def create_db_engine(database_url: str | None = None) -> Engine:
    """Create a SQLAlchemy engine using an explicit or configured URL."""
    resolved_url = database_url or get_settings().database_url
    _prepare_sqlite_directory(resolved_url)

    connect_args = (
        {"check_same_thread": False}
        if make_url(resolved_url).get_backend_name() == "sqlite"
        else {}
    )
    db_engine = create_engine(resolved_url, connect_args=connect_args)
    if make_url(resolved_url).get_backend_name() == "sqlite":
        event.listen(
            db_engine,
            "connect",
            lambda connection, _: connection.execute("PRAGMA foreign_keys=ON"),
        )
    return db_engine


engine = create_db_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

# Per-user engines are created lazily and cached by username so concurrent
# requests for the same account share one engine (each points at that user's
# own SQLite file, so data never crosses tenants).
_tenant_factories: dict[str, sessionmaker[Session]] = {}
_tenant_factories_lock = threading.Lock()


def get_session_factory() -> sessionmaker[Session]:
    """Return the session factory for the active tenant, else the global one."""
    from radar.tenant import current_tenant

    tenant = current_tenant()
    if tenant is None:
        return SessionLocal
    factory = _tenant_factories.get(tenant.username)
    if factory is not None:
        return factory
    with _tenant_factories_lock:
        factory = _tenant_factories.get(tenant.username)
        if factory is None:
            tenant_engine = create_db_engine(tenant.db_url)
            init_database(tenant_engine)
            factory = sessionmaker(
                bind=tenant_engine, autoflush=False, expire_on_commit=False
            )
            _tenant_factories[tenant.username] = factory
    return factory


def init_database(db_engine: Engine | None = None) -> None:
    """Create all currently registered tables, then apply pending migrations."""
    from radar import models as _models  # noqa: F401

    active_engine = db_engine or engine
    Base.metadata.create_all(bind=active_engine)
    run_migrations(active_engine)


def _migrate_source_traceability_columns(db_engine: Engine) -> None:
    """Backfill additive source columns for existing local demo databases."""

    inspector = inspect(db_engine)
    if "sources" not in inspector.get_table_names():
        return
    existing = {column["name"] for column in inspector.get_columns("sources")}
    statements = {
        "venue": "ALTER TABLE sources ADD COLUMN venue TEXT",
        "publication_type": (
            "ALTER TABLE sources ADD COLUMN publication_type VARCHAR DEFAULT 'preprint'"
        ),
        "pdf_url": "ALTER TABLE sources ADD COLUMN pdf_url VARCHAR",
    }
    missing = [statement for name, statement in statements.items() if name not in existing]
    if not missing:
        return
    with db_engine.begin() as connection:
        for statement in missing:
            connection.execute(text(statement))


def _migrate_model_run_traceability_columns(db_engine: Engine) -> None:
    """Backfill case/scan traceability columns on legacy model_runs tables."""

    inspector = inspect(db_engine)
    if "model_runs" not in inspector.get_table_names():
        return
    existing = {column["name"] for column in inspector.get_columns("model_runs")}
    statements = {
        "case_id": "ALTER TABLE model_runs ADD COLUMN case_id VARCHAR",
        "scan_run_id": "ALTER TABLE model_runs ADD COLUMN scan_run_id VARCHAR",
    }
    missing = [statement for name, statement in statements.items() if name not in existing]
    with db_engine.begin() as connection:
        for statement in missing:
            connection.execute(text(statement))
        connection.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_model_runs_case_id "
                "ON model_runs (case_id)"
            )
        )
        connection.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_model_runs_scan_run_id "
                "ON model_runs (scan_run_id)"
            )
        )


def _migrate_scan_run_updated_at(db_engine: Engine) -> None:
    """Add the ScanRun heartbeat column used by interrupted-scan recovery."""

    inspector = inspect(db_engine)
    if "scan_runs" not in inspector.get_table_names():
        return
    existing = {column["name"] for column in inspector.get_columns("scan_runs")}
    if "updated_at" in existing:
        return
    with db_engine.begin() as connection:
        connection.execute(
            text("ALTER TABLE scan_runs ADD COLUMN updated_at DATETIME")
        )
        connection.execute(
            text(
                "UPDATE scan_runs SET updated_at = created_at "
                "WHERE updated_at IS NULL"
            )
        )


def _migrate_action_item_advice_source(db_engine: Engine) -> None:
    """Track whether an action's text came from the LLM or rule templates."""

    inspector = inspect(db_engine)
    if "action_items" not in inspector.get_table_names():
        return
    existing = {column["name"] for column in inspector.get_columns("action_items")}
    if "advice_source" in existing:
        return
    with db_engine.begin() as connection:
        connection.execute(
            text(
                "ALTER TABLE action_items ADD COLUMN advice_source VARCHAR "
                "DEFAULT 'rule'"
            )
        )


def _migrate_source_cited_by_count(db_engine: Engine) -> None:
    """Add the citation-count quality signal populated by the OpenAlex source."""

    inspector = inspect(db_engine)
    if "sources" not in inspector.get_table_names():
        return
    existing = {column["name"] for column in inspector.get_columns("sources")}
    if "cited_by_count" in existing:
        return
    with db_engine.begin() as connection:
        connection.execute(
            text("ALTER TABLE sources ADD COLUMN cited_by_count INTEGER")
        )


def _migrate_claim_attribution_columns(db_engine: Engine) -> None:
    """Add Solution 1-5 claim pipeline columns: author_attribution, section_id, qualifiers, track_state, claim_role, parent_claim_id."""

    inspector = inspect(db_engine)
    if "claim_revisions" in inspector.get_table_names():
        revision_cols = {c["name"] for c in inspector.get_columns("claim_revisions")}
        with db_engine.begin() as connection:
            if "author_attribution" not in revision_cols:
                connection.execute(text(
                    "ALTER TABLE claim_revisions ADD COLUMN author_attribution VARCHAR DEFAULT 'uncertain'"
                ))
            if "section_id" not in revision_cols:
                connection.execute(text(
                    "ALTER TABLE claim_revisions ADD COLUMN section_id VARCHAR"
                ))
            if "qualifiers_json" not in revision_cols:
                connection.execute(text(
                    "ALTER TABLE claim_revisions ADD COLUMN qualifiers_json JSON DEFAULT '[]'"
                ))

    if "claims" in inspector.get_table_names():
        claim_cols = {c["name"] for c in inspector.get_columns("claims")}
        with db_engine.begin() as connection:
            if "track_state" not in claim_cols:
                connection.execute(text(
                    "ALTER TABLE claims ADD COLUMN track_state VARCHAR DEFAULT 'new'"
                ))
            if "parent_claim_id" not in claim_cols:
                connection.execute(text(
                    "ALTER TABLE claims ADD COLUMN parent_claim_id VARCHAR"
                ))
            if "claim_role" not in claim_cols:
                connection.execute(text(
                    "ALTER TABLE claims ADD COLUMN claim_role VARCHAR DEFAULT 'sub_claim'"
                ))


def _migrate_snapshot_full_text_hash(db_engine: Engine) -> None:
    """Add full_text_hash to source_snapshots; restore content_hash identity.

    Legacy bug: _store_full_text overwrote content_hash (the abstract identity)
    with the full-text hash, making every enriched snapshot a cache miss on the
    next scan. This migration recomputes content_hash from the stored abstract
    and moves the full-text hash into its own column.
    """

    inspector = inspect(db_engine)
    if "source_snapshots" not in inspector.get_table_names():
        return
    existing = {column["name"] for column in inspector.get_columns("source_snapshots")}
    with db_engine.begin() as connection:
        if "full_text_hash" not in existing:
            connection.execute(
                text("ALTER TABLE source_snapshots ADD COLUMN full_text_hash VARCHAR")
            )
        rows = connection.execute(
            text(
                "SELECT id, abstract, content_text, content_hash, version_label "
                "FROM source_snapshots"
            )
        ).fetchall()
        for row_id, abstract, content_text, content_hash, version_label in rows:
            abstract_hash = hashlib.sha256((abstract or "").strip().encode()).hexdigest()
            full_text_hash = None
            if version_label and version_label.endswith(":public-pdf"):
                full_text_hash = hashlib.sha256((content_text or "").encode()).hexdigest()
            connection.execute(
                text(
                    "UPDATE source_snapshots SET content_hash = :ch, full_text_hash = :fh "
                    "WHERE id = :id"
                ),
                {"ch": abstract_hash, "fh": full_text_hash, "id": row_id},
            )


def _migrate_g0_feedback_table(db_engine: Engine) -> None:
    """Create G0Feedback table for structured claim feedback tracking."""

    inspector = inspect(db_engine)
    if "g0_feedback" in inspector.get_table_names():
        return
    from radar.models import G0Feedback

    Base.metadata.create_all(bind=db_engine, tables=[G0Feedback.__table__])


def _migrate_retrieval_receipts_table(db_engine: Engine) -> None:
    """Create the bounded-search provenance table for existing databases."""

    from radar.models import RetrievalReceipt

    Base.metadata.create_all(bind=db_engine, tables=[RetrievalReceipt.__table__])


def _migrate_source_cs_ai_metadata(db_engine: Engine) -> None:
    """Add CS/AI domain metadata columns to existing sources tables."""

    inspector = inspect(db_engine)
    if "sources" not in inspector.get_table_names():
        return
    existing = {column["name"] for column in inspector.get_columns("sources")}
    statements = {
        "arxiv_primary_category": "ALTER TABLE sources ADD COLUMN arxiv_primary_category VARCHAR",
        "fields_of_study_json": "ALTER TABLE sources ADD COLUMN fields_of_study_json JSON DEFAULT '[]'",
        "ccf_rank": "ALTER TABLE sources ADD COLUMN ccf_rank VARCHAR",
    }
    missing = [statement for name, statement in statements.items() if name not in existing]
    if not missing:
        return
    with db_engine.begin() as connection:
        for statement in missing:
            connection.execute(text(statement))


def _migrate_source_kind_column(db_engine: Engine) -> None:
    """Add a stable source label used by API and UI source rendering."""

    inspector = inspect(db_engine)
    if "sources" not in inspector.get_table_names():
        return
    existing = {column["name"] for column in inspector.get_columns("sources")}
    if "source_kind" in existing:
        return
    with db_engine.begin() as connection:
        connection.execute(
            text(
                "ALTER TABLE sources ADD COLUMN source_kind VARCHAR DEFAULT 'unknown'"
            )
        )
        if {"arxiv_id", "external_id"} <= existing:
            connection.execute(
                text(
                    "UPDATE sources SET source_kind = CASE "
                    "WHEN arxiv_id IS NOT NULL THEN 'arxiv' "
                    "WHEN external_id LIKE 'openalex:%' THEN 'openalex' "
                    "WHEN external_id LIKE 'semantic_scholar:%' THEN 'semantic_scholar' "
                    "ELSE 'unknown' END"
                )
            )
        elif "arxiv_id" in existing:
            connection.execute(
                text(
                    "UPDATE sources SET source_kind = CASE "
                    "WHEN arxiv_id IS NOT NULL THEN 'arxiv' ELSE 'unknown' END"
                )
            )
        elif "external_id" in existing:
            connection.execute(
                text(
                    "UPDATE sources SET source_kind = CASE "
                    "WHEN external_id LIKE 'openalex:%' THEN 'openalex' "
                    "WHEN external_id LIKE 'semantic_scholar:%' THEN 'semantic_scholar' "
                    "ELSE 'unknown' END"
                )
            )


def _migrate_impact_strategy_payload(db_engine: Engine) -> None:
    """Add strategy_payload_json column to impact_candidates table."""
    inspector = inspect(db_engine)
    if "impact_candidates" not in inspector.get_table_names():
        return
    existing = {column["name"] for column in inspector.get_columns("impact_candidates")}
    if "strategy_payload_json" in existing:
        return
    with db_engine.begin() as connection:
        connection.execute(
            text("ALTER TABLE impact_candidates ADD COLUMN strategy_payload_json JSON DEFAULT '{}'")
        )


# Each entry is (version, name, idempotent migration callable).
Migration = tuple[int, str, Callable[[Engine], None]]
MIGRATIONS: list[Migration] = [
    (1, "source_traceability_columns", _migrate_source_traceability_columns),
    (2, "model_run_traceability_columns", _migrate_model_run_traceability_columns),
    (3, "scan_run_updated_at", _migrate_scan_run_updated_at),
    (4, "action_item_advice_source", _migrate_action_item_advice_source),
    (5, "source_cited_by_count", _migrate_source_cited_by_count),
    (6, "claim_attribution_columns", _migrate_claim_attribution_columns),
    (7, "g0_feedback_table", _migrate_g0_feedback_table),
    (8, "snapshot_full_text_hash", _migrate_snapshot_full_text_hash),
    (9, "retrieval_receipts_table", _migrate_retrieval_receipts_table),
    (10, "source_kind_column", _migrate_source_kind_column),
    (11, "source_cs_ai_metadata", _migrate_source_cs_ai_metadata),
    (12, "impact_strategy_payload", _migrate_impact_strategy_payload),
]


def run_migrations(db_engine: Engine) -> list[int]:
    """Apply registered migrations in version order; return newly applied versions."""

    with db_engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE IF NOT EXISTS schema_migrations ("
                "version INTEGER PRIMARY KEY, applied_at VARCHAR NOT NULL)"
            )
        )
        applied = {
            row[0]
            for row in connection.execute(text("SELECT version FROM schema_migrations"))
        }
    newly_applied: list[int] = []
    for version, _name, migrate in sorted(MIGRATIONS, key=lambda item: item[0]):
        if version in applied:
            continue
        migrate(db_engine)
        with db_engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO schema_migrations (version, applied_at) "
                    "VALUES (:version, :applied_at)"
                ),
                {
                    "version": version,
                    "applied_at": datetime.now(timezone.utc).isoformat(),
                },
            )
        newly_applied.append(version)
    return newly_applied


@contextmanager
def session_scope(
    session_factory: sessionmaker[Session] | None = None,
) -> Iterator[Session]:
    """Provide a transactional session with rollback on failure.

    Resolves the active tenant's factory when none is passed, so api.py call
    sites do not need to thread the per-user factory explicitly.
    """
    factory = session_factory or get_session_factory()
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
