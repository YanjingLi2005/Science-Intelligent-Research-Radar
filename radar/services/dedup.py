"""Cross-source SourceRecord deduplication.

Same paper can arrive from arXiv, OpenAlex, Semantic Scholar, or PubMed with
different external ids but the same DOI, arXiv id, or title. This class keeps
a multi-key index so those records collapse into one SourceRecord.
"""

from __future__ import annotations

from radar.schemas import SourceRecord


def _normalize_title(value: str) -> str:
    return " ".join(
        "".join(character.lower() if character.isalnum() else " " for character in value).split()
    )


class SourceDeduplicator:
    def __init__(self) -> None:
        self._records: dict[str, SourceRecord] = {}
        self._doi: dict[str, str] = {}
        self._arxiv: dict[str, str] = {}
        self._title: dict[str, str] = {}
        self._duplicate_count = 0

    @staticmethod
    def _primary_key(record: SourceRecord) -> str:
        if record.external_id:
            return record.external_id
        if record.doi:
            return f"doi:{record.doi.lower()}"
        return f"title:{_normalize_title(record.title)}"

    def add(self, record: SourceRecord) -> None:
        existing_key = self._find_existing(record)
        if existing_key is None:
            key = self._primary_key(record)
            self._records[key] = record
            self._index(record, key)
            return
        self._duplicate_count += 1
        merged = self._merge(self._records[existing_key], record)
        self._records[existing_key] = merged
        self._index(merged, existing_key)

    def _find_existing(self, record: SourceRecord) -> str | None:
        if record.doi:
            key = self._doi.get(record.doi.lower())
            if key:
                return key
        if record.arxiv_id:
            key = self._arxiv.get(record.arxiv_id)
            if key:
                return key
        normalized = _normalize_title(record.title)
        if normalized:
            key = self._title.get(normalized)
            if key:
                return key
        return None

    def _index(self, record: SourceRecord, key: str) -> None:
        if record.doi:
            self._doi[record.doi.lower()] = key
        if record.arxiv_id:
            self._arxiv[record.arxiv_id] = key
        normalized = _normalize_title(record.title)
        if normalized:
            self._title[normalized] = key

    @staticmethod
    def _merge(existing: SourceRecord, new: SourceRecord) -> SourceRecord:
        """Merge two records for the same paper, preferring richer metadata."""
        authors = list(dict.fromkeys([*existing.authors, *new.authors]))
        fields = list(dict.fromkeys([*existing.fields_of_study, *new.fields_of_study]))
        abstract = existing.abstract or new.abstract
        if new.abstract and len(new.abstract) > len(existing.abstract or ""):
            abstract = new.abstract
        return SourceRecord(
            source_kind=existing.source_kind or new.source_kind,
            external_id=existing.external_id or new.external_id,
            title=existing.title or new.title,
            authors=authors,
            abstract=abstract,
            url=existing.url or new.url,
            published_at=existing.published_at or new.published_at,
            doi=existing.doi or new.doi,
            arxiv_id=existing.arxiv_id or new.arxiv_id,
            license=existing.license or new.license,
            venue=existing.venue or new.venue,
            publication_type=(
                new.publication_type
                if new.publication_type != "preprint"
                else existing.publication_type
            ),
            pdf_url=existing.pdf_url or new.pdf_url,
            cited_by_count=existing.cited_by_count or new.cited_by_count,
            arxiv_primary_category=existing.arxiv_primary_category or new.arxiv_primary_category,
            fields_of_study=fields,
            ccf_rank=existing.ccf_rank or new.ccf_rank,
        )

    @property
    def duplicate_count(self) -> int:
        return self._duplicate_count

    def values(self) -> list[SourceRecord]:
        return list(self._records.values())
