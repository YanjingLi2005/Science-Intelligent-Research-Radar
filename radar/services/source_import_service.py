"""Import a single external paper into a case (browser-extension entry point).

The extension/user supplies a URL or DOI; the service resolves it through the
same academic adapters, stores it as a Source/SourceSnapshot, and returns the
new source so the frontend/MCP can immediately show it.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from radar.adapters.arxiv import ArxivSearchAdapter
from radar.adapters.openalex import OpenAlexSearchAdapter
from radar.config import Settings, get_settings
from radar.db import SessionLocal, session_scope
from radar.models import ResearchCase, Source, SourceSnapshot
from radar.schemas import SourceRecord


def _extract_doi(value: str | None) -> str | None:
    if not value:
        return None
    match = re.search(r"(10\.\d{4,9}/[^\s?#]+)", value, re.IGNORECASE)
    if not match:
        return None
    return match.group(1).rstrip(".,;)")


def _extract_arxiv_id(value: str | None) -> str | None:
    if not value:
        return None
    match = re.search(
        r"arxiv\.org/(?:abs|pdf)/([0-9]{4}\.[0-9]{4,5}|[a-z\-]+(?:\.[A-Z]{2})?/\d{7})",
        value,
        re.IGNORECASE,
    )
    return match.group(1) if match else None


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


class SourceImportService:
    def __init__(
        self,
        session_factory: sessionmaker[Session] = SessionLocal,
        *,
        settings: Settings | None = None,
    ):
        self.session_factory = session_factory
        self.settings = settings or get_settings()

    def import_source(
        self, case_id: str, *, url: str | None = None, doi: str | None = None
    ) -> dict:
        with session_scope(self.session_factory) as session:
            if session.get(ResearchCase, case_id) is None:
                raise LookupError(f"case not found: {case_id}")

        doi = doi or _extract_doi(url)
        arxiv_id = _extract_arxiv_id(url)

        record: SourceRecord | None = None
        if arxiv_id:
            adapter = ArxivSearchAdapter(self.settings.data_dir / "cache" / "arxiv")
            records = adapter.lookup([arxiv_id])
            record = records[0] if records else None
        if record is None and doi:
            record = OpenAlexSearchAdapter().get_work_by_doi(doi)
        if record is None:
            raise ValueError(
                "could not resolve source from the given URL/DOI; "
                "provide an arXiv URL or a DOI"
            )

        snapshot_ids = self._store_records([record])
        if not snapshot_ids:
            raise ValueError("resolved source has no usable abstract")

        return {
            "source_id": snapshot_ids[0],
            "title": record.title,
            "url": record.url,
            "doi": record.doi,
            "arxiv_id": record.arxiv_id,
            "source_kind": record.source_kind,
        }

    def _store_records(self, records: list[SourceRecord]) -> list[str]:
        snapshot_ids: list[str] = []
        with session_scope(self.session_factory) as session:
            for record in records:
                content = (record.abstract or "").strip()
                if not content:
                    continue
                source_kind = record.source_kind or record.external_id.split(":", 1)[0]
                source = session.scalar(
                    select(Source).where(Source.external_id == record.external_id)
                )
                if source is None:
                    source = Source(
                        id=str(uuid4()),
                        external_id=record.external_id,
                        source_kind=source_kind,
                        title=record.title,
                        authors_json=record.authors,
                        published_at=_parse_datetime(record.published_at),
                        url=record.url,
                        doi=record.doi,
                        arxiv_id=record.arxiv_id,
                        license=record.license,
                        venue=record.venue,
                        publication_type=record.publication_type,
                        pdf_url=record.pdf_url,
                        cited_by_count=record.cited_by_count,
                        integrity_state="normal",
                        arxiv_primary_category=record.arxiv_primary_category,
                        fields_of_study_json=record.fields_of_study,
                        ccf_rank=record.ccf_rank,
                    )
                    session.add(source)
                    session.flush()
                else:
                    source.source_kind = source_kind or source.source_kind
                    source.title = record.title or source.title
                    source.authors_json = record.authors or source.authors_json
                    source.published_at = _parse_datetime(record.published_at) or source.published_at
                    source.url = record.url or source.url
                    source.doi = record.doi or source.doi
                    source.arxiv_id = record.arxiv_id or source.arxiv_id
                    source.license = record.license or source.license
                    source.venue = record.venue or source.venue
                    source.publication_type = record.publication_type or source.publication_type
                    source.pdf_url = record.pdf_url or source.pdf_url
                    if record.cited_by_count is not None:
                        source.cited_by_count = record.cited_by_count
                    if record.arxiv_primary_category:
                        source.arxiv_primary_category = record.arxiv_primary_category
                    if record.fields_of_study:
                        source.fields_of_study_json = list(
                            dict.fromkeys([*source.fields_of_study_json, *record.fields_of_study])
                        )
                    if record.ccf_rank:
                        source.ccf_rank = record.ccf_rank
                content_hash = hashlib.sha256(content.encode()).hexdigest()
                snapshot = session.scalar(
                    select(SourceSnapshot).where(
                        SourceSnapshot.source_id == source.id,
                        SourceSnapshot.content_hash == content_hash,
                    )
                )
                if snapshot is None:
                    snapshot = SourceSnapshot(
                        id=str(uuid4()),
                        source_id=source.id,
                        version_label=record.published_at or "imported",
                        title=record.title,
                        abstract=record.abstract,
                        content_text=content,
                        content_hash=content_hash,
                        event_time=_parse_datetime(record.published_at),
                        observed_at=datetime.now(timezone.utc),
                    )
                    session.add(snapshot)
                    session.flush()
                snapshot_ids.append(snapshot.id)
        return snapshot_ids
