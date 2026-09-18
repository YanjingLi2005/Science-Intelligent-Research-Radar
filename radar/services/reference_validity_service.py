"""Layer 1: deterministic external checks for reference bibliographic validity."""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from typing import Any

from radar.adapters.arxiv import ArxivSearchAdapter
from radar.adapters.crossref import CrossrefIntegrityAdapter
from radar.adapters.crossref_search import CrossrefSearchAdapter
from radar.adapters.dblp import DblpSearchAdapter
from radar.adapters.openalex import OpenAlexSearchAdapter
from radar.config import get_settings
from radar.db import SessionLocal
from radar.schemas import (
    ReferenceCheckBatch,
    ReferenceCheckResult,
    ReferenceEntry,
    ReferenceMatch,
    SourceRecord,
    WatchQuery,
)


def _normalize_text(value: str | None) -> str:
    return " ".join(
        "".join(character.lower() if character.isalnum() else " " for character in str(value or "")).split()
    )


def _normalize_doi(value: str | None) -> str | None:
    if not value:
        return None
    normalized = str(value).strip()
    normalized = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", normalized, flags=re.I)
    normalized = re.sub(r"^doi:\s*", "", normalized, flags=re.I)
    normalized = normalized.rstrip(".,;:)").lower()
    return normalized or None


def _normalize_arxiv_id(value: str | None) -> str | None:
    if not value:
        return None
    normalized = str(value).strip()
    normalized = re.sub(
        r"^https?://arxiv\.org/(?:abs|pdf)/", "", normalized, flags=re.I
    )
    normalized = re.sub(r"^arxiv:\s*", "", normalized, flags=re.I)
    return normalized.rstrip(".,;:)").lower() or None


def _year_from(value: Any) -> int | None:
    if isinstance(value, int):
        return value
    match = re.search(r"\b(?:18|19|20|21)\d{2}\b", str(value or ""))
    return int(match.group(0)) if match else None


def _surname(value: str) -> str:
    normalized = _normalize_text(value)
    if not normalized:
        return ""
    if "," in value:
        return _normalize_text(value.split(",", 1)[0])
    return normalized.split()[-1]


def _title_similarity(query: str, candidate: str) -> float:
    query_tokens = set(_normalize_text(query).split())
    candidate_tokens = set(_normalize_text(candidate).split())
    if not query_tokens or not candidate_tokens:
        return 0.0
    return len(query_tokens & candidate_tokens) / max(len(query_tokens | candidate_tokens), 1)


def _is_title_good(entry_title: str, candidate_title: str, similarity: float) -> bool:
    normalized_entry = _normalize_text(entry_title)
    normalized_candidate = _normalize_text(candidate_title)
    return bool(normalized_entry and normalized_entry == normalized_candidate) or similarity >= 0.85


class ReferenceValidityService:
    """Resolve references against public bibliographic sources.

    The service is intentionally stateless with respect to the database.  The
    session factory is retained for consistency with the other application
    services and for future persistence of audit receipts.
    """

    def __init__(self, session_factory=SessionLocal, adapters=None):
        self.session_factory = session_factory
        self._adapters = adapters

    def _get_adapters(self) -> Mapping[str, object]:
        if self._adapters is None:
            settings = get_settings()
            self._adapters = {
                "openalex": OpenAlexSearchAdapter(),
                "crossref": CrossrefIntegrityAdapter(),
                "dblp": DblpSearchAdapter(),
                "arxiv": ArxivSearchAdapter(settings.data_dir / "cache" / "arxiv"),
                # Crossref's integrity client intentionally has a narrow
                # metadata API; this companion is only built for title search.
                "crossref_search": CrossrefSearchAdapter(),
            }
        if isinstance(self._adapters, Mapping):
            return self._adapters
        return {
            getattr(adapter, "source_kind", str(index)): adapter
            for index, adapter in enumerate(self._adapters)
        }

    @staticmethod
    def _add_reason(reasons: list[str], reason: str) -> None:
        if reason not in reasons:
            reasons.append(reason)

    @staticmethod
    def _add_source(queried_sources: list[str], source: str) -> None:
        if source not in queried_sources:
            queried_sources.append(source)

    def _invoke(
        self,
        source: str,
        queried_sources: list[str],
        reasons: list[str],
        callback: Callable[[], Any],
    ) -> tuple[bool, Any]:
        self._add_source(queried_sources, source)
        try:
            return True, callback()
        except Exception:
            # A public source is optional: one timeout, rate limit, or malformed
            # response must not hide matches from the other sources.
            self._add_reason(reasons, "source_unavailable")
            return False, None

    @staticmethod
    def _authors_from(value: Any) -> list[str]:
        if value is None:
            return []
        values = value if isinstance(value, list) else [value]
        authors: list[str] = []
        for author in values:
            if isinstance(author, dict):
                author = author.get("name") or author.get("full_name") or author.get("text")
            name = str(author or "").strip()
            if name:
                authors.append(name)
        return authors

    def _as_match(
        self,
        candidate: Any,
        source_kind: str,
        entry: ReferenceEntry | None = None,
    ) -> ReferenceMatch | None:
        if candidate is None:
            return None
        if isinstance(candidate, ReferenceMatch):
            match = candidate.model_copy(deep=True)
        else:
            if isinstance(candidate, SourceRecord):
                data = candidate.model_dump()
            elif hasattr(candidate, "model_dump"):
                data = candidate.model_dump()
            elif isinstance(candidate, dict):
                data = candidate
            else:
                return None

            nested = data.get("message") if isinstance(data.get("message"), dict) else data
            title = str(
                nested.get("title")
                or nested.get("display_name")
                or (entry.title if entry else "")
            ).strip()
            doi = _normalize_doi(nested.get("doi") or nested.get("DOI"))
            arxiv_id = _normalize_arxiv_id(
                nested.get("arxiv_id") or nested.get("arxiv") or nested.get("eprint")
            )
            external_id = str(
                nested.get("external_id")
                or nested.get("id")
                or nested.get("key")
                or (f"{source_kind}:{doi or arxiv_id}" if (doi or arxiv_id) else "")
            ).strip()
            if external_id and "://" in external_id:
                external_id = external_id.rsplit("/", 1)[-1]
            if external_id and not external_id.startswith(f"{source_kind}:"):
                external_id = f"{source_kind}:{external_id}"
            authors = self._authors_from(
                nested.get("authors") or nested.get("author")
            )
            year = _year_from(
                nested.get("year")
                or nested.get("published_at")
                or nested.get("publication_date")
                or nested.get("issued")
            )
            venue = str(
                nested.get("venue")
                or nested.get("journal")
                or nested.get("container-title")
                or nested.get("booktitle")
                or ""
            ).strip() or None
            url = str(
                nested.get("url")
                or nested.get("URL")
                or nested.get("publisher_url")
                or ""
            ).strip()
            if not title or not external_id:
                return None
            match = ReferenceMatch(
                source_kind=str(nested.get("source_kind") or source_kind),
                external_id=external_id,
                title=title,
                authors=authors,
                year=year,
                venue=venue,
                doi=doi,
                arxiv_id=arxiv_id,
                url=url,
            )
        if not match.source_kind or match.source_kind == "unknown":
            match.source_kind = source_kind
        match.doi = _normalize_doi(match.doi)
        match.arxiv_id = _normalize_arxiv_id(match.arxiv_id)
        if not match.title and entry:
            match.title = entry.title
        if not match.external_id:
            identifier = match.doi or match.arxiv_id or _normalize_text(match.title)
            match.external_id = f"{source_kind}:{identifier}"
        match.url = self._canonical_url(match)
        return match

    @staticmethod
    def _canonical_url(match: ReferenceMatch) -> str:
        if match.doi:
            return f"https://doi.org/{match.doi}"
        if match.source_kind == "openalex":
            identifier = match.external_id.split(":", 1)[-1]
            if identifier:
                return f"https://openalex.org/{identifier}"
        if match.source_kind == "dblp":
            key = match.external_id.split(":", 1)[-1]
            if key:
                return f"https://dblp.org/rec/{key}"
        if match.source_kind == "arxiv" and match.arxiv_id:
            return f"https://arxiv.org/abs/{match.arxiv_id}"
        return match.url

    @staticmethod
    def _flatten_candidates(value: Any) -> list[Any]:
        if value is None:
            return []
        return value if isinstance(value, list) else [value]

    def _append_candidates(
        self,
        output: list[ReferenceMatch],
        value: Any,
        source_kind: str,
        entry: ReferenceEntry,
        reasons: list[str],
    ) -> None:
        for candidate in self._flatten_candidates(value):
            try:
                match = self._as_match(candidate, source_kind, entry)
            except Exception:
                self._add_reason(reasons, "source_unavailable")
                continue
            if match is not None:
                output.append(match)

    def _search_crossref(
        self, adapter: object, title: str, max_results: int
    ) -> Any:
        search = getattr(adapter, "search")
        try:
            return search(title, max_results=max_results)
        except TypeError:
            return search(
                "reference-validity",
                WatchQuery(query=title, max_results=max_results),
            )

    def _search_dblp(self, adapter: object, title: str, max_results: int) -> Any:
        search = getattr(adapter, "search")
        try:
            return search(title, max_results=max_results)
        except TypeError:
            return search("reference-validity", WatchQuery(query=title, max_results=max_results))

    def _title_candidates(
        self,
        entry: ReferenceEntry,
        adapters: Mapping[str, object],
        queried_sources: list[str],
        reasons: list[str],
        candidates: list[ReferenceMatch],
    ) -> None:
        if not entry.title.strip():
            self._add_reason(reasons, "metadata_incomplete")
            return

        openalex = adapters.get("openalex")
        if openalex is not None:
            if callable(getattr(openalex, "get_work_by_title", None)):
                ok, value = self._invoke(
                    "openalex", queried_sources, reasons,
                    lambda: openalex.get_work_by_title(entry.title),
                )
            elif callable(getattr(openalex, "resolve_work", None)):
                # Older/injected OpenAlex clients may only expose the resolver.
                # It is useful when it returns a structured record, while a bare
                # work id is deliberately not treated as bibliographic proof.
                ok, value = self._invoke(
                    "openalex", queried_sources, reasons,
                    lambda: openalex.resolve_work(entry.title),
                )
            else:
                ok, value = True, None
            if ok:
                self._append_candidates(candidates, value, "openalex", entry, reasons)

        crossref = adapters.get("crossref")
        crossref_search = crossref if crossref is not None and callable(getattr(crossref, "search", None)) else adapters.get("crossref_search")
        if crossref_search is not None and callable(getattr(crossref_search, "search", None)):
            ok, value = self._invoke(
                "crossref", queried_sources, reasons,
                lambda: self._search_crossref(crossref_search, entry.title, 5),
            )
            if ok:
                self._append_candidates(candidates, value, "crossref", entry, reasons)

        dblp = adapters.get("dblp")
        if dblp is not None:
            if callable(getattr(dblp, "resolve_by_title", None)):
                ok, value = self._invoke(
                    "dblp", queried_sources, reasons,
                    lambda: dblp.resolve_by_title(entry.title),
                )
            elif callable(getattr(dblp, "search", None)):
                ok, value = self._invoke(
                    "dblp", queried_sources, reasons,
                    lambda: self._search_dblp(dblp, entry.title, 5),
                )
            else:
                ok, value = True, None
            if ok:
                self._append_candidates(candidates, value, "dblp", entry, reasons)

    def _score(self, entry: ReferenceEntry, match: ReferenceMatch) -> tuple[float, bool, bool, list[str]]:
        score = 0.0
        matched_fields: list[str] = []
        identifier_exact = False
        if entry.doi and _normalize_doi(entry.doi) == _normalize_doi(match.doi):
            score += 0.45
            identifier_exact = True
            matched_fields.append("doi")
        elif entry.arxiv_id and _normalize_arxiv_id(entry.arxiv_id) == _normalize_arxiv_id(match.arxiv_id):
            score += 0.45
            identifier_exact = True
            matched_fields.append("arxiv_id")

        similarity = _title_similarity(entry.title, match.title)
        title_good = _is_title_good(entry.title, match.title, similarity)
        if _normalize_text(entry.title) and _normalize_text(entry.title) == _normalize_text(match.title):
            score += 0.30
            matched_fields.append("title")
        elif similarity >= 0.85:
            score += 0.30
            matched_fields.append("title")
        elif similarity >= 0.60:
            score += 0.18
            matched_fields.append("title")

        entry_surnames = {_surname(author) for author in entry.authors if _surname(author)}
        match_surnames = {_surname(author) for author in match.authors if _surname(author)}
        shared_surnames = entry_surnames & match_surnames
        if shared_surnames:
            score += 0.10
            matched_fields.append("authors")
            if len(shared_surnames) / max(len(entry_surnames), 1) >= 0.5:
                score += 0.10
        if entry.year is not None and match.year == entry.year:
            score += 0.15
            matched_fields.append("year")
        if entry.venue and match.venue and _normalize_text(entry.venue) == _normalize_text(match.venue):
            score += 0.10
            matched_fields.append("venue")
        return min(round(score, 3), 1.0), identifier_exact, title_good, matched_fields

    def check_entry(self, entry: ReferenceEntry) -> ReferenceCheckResult:
        entry = entry if isinstance(entry, ReferenceEntry) else ReferenceEntry.model_validate(entry)
        adapters = self._get_adapters()
        candidates: list[ReferenceMatch] = []
        queried_sources: list[str] = []
        reasons: list[str] = []

        if entry.doi and entry.doi.strip():
            openalex = adapters.get("openalex")
            openalex_value = None
            if openalex is not None and callable(getattr(openalex, "get_work_by_doi", None)):
                openalex_ok, openalex_value = self._invoke(
                    "openalex", queried_sources, reasons,
                    lambda: openalex.get_work_by_doi(entry.doi or ""),
                )
                if openalex_ok:
                    self._append_candidates(candidates, openalex_value, "openalex", entry, reasons)

            has_exact_doi = any(
                _normalize_doi(candidate.doi) == _normalize_doi(entry.doi)
                for candidate in candidates
            )
            if openalex_value is None or not has_exact_doi:
                crossref = adapters.get("crossref")
                if crossref is not None and callable(getattr(crossref, "metadata", None)):
                    crossref_ok, metadata = self._invoke(
                        "crossref", queried_sources, reasons,
                        lambda: crossref.metadata(entry.doi or ""),
                    )
                    if crossref_ok:
                        self._append_candidates(candidates, metadata, "crossref", entry, reasons)

            if not any(
                _normalize_doi(candidate.doi) == _normalize_doi(entry.doi)
                for candidate in candidates
            ):
                self._title_candidates(entry, adapters, queried_sources, reasons, candidates)
        else:
            arxiv = adapters.get("arxiv")
            if entry.arxiv_id and arxiv is not None and callable(getattr(arxiv, "lookup", None)):
                ok, value = self._invoke(
                    "arxiv", queried_sources, reasons,
                    lambda: arxiv.lookup([entry.arxiv_id or ""]),
                )
                if ok:
                    self._append_candidates(candidates, value, "arxiv", entry, reasons)
            self._title_candidates(entry, adapters, queried_sources, reasons, candidates)

        unique_candidates: list[ReferenceMatch] = []
        seen: set[str] = set()
        for candidate in candidates:
            key = candidate.doi or candidate.arxiv_id or candidate.external_id
            if key in seen:
                continue
            seen.add(key)
            unique_candidates.append(candidate)

        scored: list[tuple[float, bool, bool, list[str], ReferenceMatch]] = []
        for candidate in unique_candidates:
            score, identifier_exact, title_good, fields = self._score(entry, candidate)
            candidate.matched_fields = fields
            candidate.confidence = score
            scored.append((score, identifier_exact, title_good, fields, candidate))
        scored.sort(key=lambda item: (-item[0], item[4].external_id))

        best = scored[0] if scored else None
        if best is None:
            self._add_reason(reasons, "not_found")
            confidence = 0.0
            matched = None
            status = "unverified"
        else:
            score, identifier_exact, title_good, _, matched = best
            if identifier_exact:
                score = max(score, 0.85)
                matched.confidence = score
            confidence = score
            verified = identifier_exact or (title_good and confidence >= 0.60)
            status = "verified" if verified else "unverified"
            if (
                not verified
                and len(scored) > 1
                and scored[1][0] >= 0.60
                and abs(scored[0][0] - scored[1][0]) < 0.05
            ):
                self._add_reason(reasons, "ambiguous_match")
            if not verified:
                if not matched.title or not entry.title:
                    self._add_reason(reasons, "metadata_incomplete")
                else:
                    self._add_reason(reasons, "title_match_low")

        return ReferenceCheckResult(
            entry=entry,
            status=status,
            confidence=confidence,
            reasons=reasons,
            matched=matched,
            queried_sources=queried_sources,
        )

    def check_batch(self, entries: list[ReferenceEntry]) -> ReferenceCheckBatch:
        results: list[ReferenceCheckResult] = []
        for raw_entry in entries:
            try:
                results.append(self.check_entry(raw_entry))
            except Exception:
                # Keep batch semantics even when an injected adapter returns an
                # object that cannot be normalized into the public contract.
                entry = raw_entry if isinstance(raw_entry, ReferenceEntry) else ReferenceEntry.model_validate(raw_entry)
                results.append(
                    ReferenceCheckResult(
                        entry=entry,
                        status="unverified",
                        reasons=["source_unavailable"],
                    )
                )
        verified = sum(result.status == "verified" for result in results)
        return ReferenceCheckBatch(
            results=results,
            checked_at=datetime.now(timezone.utc).isoformat(),
            summary={
                "total": len(results),
                "verified": verified,
                "unverified": len(results) - verified,
            },
        )
