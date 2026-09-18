"""Crossref works search adapter."""

from __future__ import annotations

import html
import re
import time

import httpx

from radar.config import get_settings
from radar.schemas import SourceRecord, WatchQuery
from radar.venue_ranks import ccf_rank_for_venue


MAX_RETRIES = 3
_XML_TAG_RE = re.compile(r"<[^>]*>")


def _strip_xml_tags(value: str | None) -> str:
    """Return Crossref's JATS/XML abstract as normalized plain text."""

    if not value:
        return ""
    text = _XML_TAG_RE.sub(" ", str(value))
    return " ".join(html.unescape(text).split())


def _date_from_parts(message: dict) -> str | None:
    date_parts = (message.get("issued") or {}).get("date-parts") or []
    if not date_parts or not date_parts[0]:
        return None
    parts = date_parts[0]
    try:
        year = int(parts[0])
        month = int(parts[1]) if len(parts) > 1 else 1
        day = int(parts[2]) if len(parts) > 2 else 1
    except (TypeError, ValueError):
        return None
    return f"{year:04d}-{month:02d}-{day:02d}"


def _first_value(value) -> str:
    if isinstance(value, list):
        value = next((item for item in value if item), "")
    return str(value or "").strip()


class CrossrefSearchAdapter:
    """Query Crossref's public works endpoint."""

    source_kind = "crossref"
    endpoint = "https://api.crossref.org/works"

    def __init__(
        self,
        timeout_seconds: float = 30.0,
        *,
        mailto: str | None = None,
    ):
        self.timeout_seconds = timeout_seconds
        self.mailto = mailto or get_settings().crossref_mailto

    @staticmethod
    def _get_with_retries(client: httpx.Client, url: str, **kwargs) -> httpx.Response:
        """GET with exponential backoff for transient HTTP/network failures."""

        delay = 1.0
        for attempt in range(MAX_RETRIES + 1):
            try:
                response = client.get(url, **kwargs)
                response.raise_for_status()
                return response
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                if (status != 429 and status < 500) or attempt == MAX_RETRIES:
                    raise
                time.sleep(delay)
                delay *= 2
            except (httpx.TimeoutException, httpx.TransportError):
                if attempt == MAX_RETRIES:
                    raise
                time.sleep(delay)
                delay *= 2
        raise AssertionError("unreachable")

    def search(self, case_id: str, watch_query: WatchQuery) -> list[SourceRecord]:
        del case_id  # Crossref is global; case scoping is handled by the caller.
        params = {
            "query": watch_query.query,
            "rows": min(watch_query.max_results, 50),
            "select": "DOI,title,author,container-title,issued,URL,abstract",
        }
        try:
            with httpx.Client(
                timeout=self.timeout_seconds,
                headers={"User-Agent": f"ResearchRadar/0.1 (mailto:{self.mailto})"},
            ) as client:
                response = self._get_with_retries(client, self.endpoint, params=params)
        except httpx.HTTPError as exc:
            raise RuntimeError(f"crossref_search_failed: {exc}") from exc
        return self._parse_records(response.json())

    def _parse_records(self, payload: dict) -> list[SourceRecord]:
        message = payload.get("message") or {}
        records: list[SourceRecord] = []
        for item in message.get("items") or []:
            doi = _first_value(item.get("DOI"))
            title = _first_value(item.get("title"))
            if not doi or not title:
                continue

            authors: list[str] = []
            for author in item.get("author") or []:
                if not isinstance(author, dict):
                    continue
                given = str(author.get("given") or "").strip()
                family = str(author.get("family") or "").strip()
                name = " ".join(part for part in (given, family) if part)
                if name:
                    authors.append(name)

            venue = _first_value(item.get("container-title")) or None
            crossref_type = str(item.get("type") or "").strip().lower()
            url = _first_value(item.get("URL")) or f"https://doi.org/{doi}"
            records.append(
                SourceRecord(
                    source_kind=self.source_kind,
                    external_id=f"crossref:{doi}",
                    title=title,
                    authors=authors,
                    abstract=_strip_xml_tags(item.get("abstract")),
                    url=url,
                    published_at=_date_from_parts(item),
                    doi=doi,
                    venue=venue,
                    publication_type=(
                        "journal_article"
                        if crossref_type == "journal-article"
                        else "other"
                    ),
                    fields_of_study=[],
                    ccf_rank=ccf_rank_for_venue(venue),
                )
            )
        return records
