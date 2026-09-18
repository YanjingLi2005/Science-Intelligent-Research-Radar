"""Optional arXiv adapter with daily cache and explicit failures."""

import hashlib
import io
import json
import re
import threading
import time
from datetime import date, datetime, timezone
from pathlib import Path

import feedparser
import httpx
from pypdf import PdfReader

from radar.schemas import SourceRecord, WatchQuery
from radar.venue_ranks import ccf_rank_for_venue


_last_request_at = 0.0
# The 3s global limiter above covers the search API only. PDF downloads get
# their own slot reservation so concurrent fetches still space request starts
# on arxiv.org without serializing the downloads themselves.
_pdf_lock = threading.Lock()
_last_pdf_request_at = 0.0
PDF_REQUEST_INTERVAL = 1.0
MAX_RETRIES = 3
STOPWORDS = {
    "about", "after", "against", "also", "among", "and", "are", "for",
    "from", "how", "into", "our", "the", "their", "this", "under", "what",
    "when", "where", "which", "with",
}


def _search_expression(query: str) -> str:
    terms: list[str] = []
    for token in re.findall(r"[A-Za-z0-9][A-Za-z0-9-]*", query.lower()):
        if len(token) < 3 or token in STOPWORDS or token in terms:
            continue
        terms.append(token)
    if not terms:
        raise ValueError("arxiv_query_has_no_search_terms")
    return " AND ".join(f"all:{term}" for term in terms[:8])


class ArxivSearchAdapter:
    source_kind = "arxiv"

    endpoint = "https://export.arxiv.org/api/query"

    def __init__(self, cache_dir: Path, timeout_seconds: float = 40.0):
        self.cache_dir = Path(cache_dir)
        self.timeout_seconds = timeout_seconds

    @staticmethod
    def _get_with_retries(client: httpx.Client, url: str, **kwargs) -> httpx.Response:
        """GET with exponential backoff on 429/5xx and transient network
        failures such as timeouts (at most MAX_RETRIES retries)."""

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
                wait = delay
                if status == 429:
                    # arXiv throttles aggressively; honor Retry-After and use
                    # a longer base backoff than for ordinary 5xx failures,
                    # but never park the scan for more than two minutes.
                    retry_after = exc.response.headers.get("Retry-After")
                    wait = max(delay * 5, float(retry_after) if retry_after and retry_after.isdigit() else 0.0)
                    wait = min(wait, 120.0)
                time.sleep(wait)
                delay *= 2
            except (httpx.TimeoutException, httpx.TransportError):
                if attempt == MAX_RETRIES:
                    raise
                time.sleep(delay)
                delay *= 2
        raise AssertionError("unreachable")

    def search(self, case_id: str, watch_query: WatchQuery) -> list[SourceRecord]:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        key = hashlib.sha256(
            f"{date.today().isoformat()}:{watch_query.query}:{watch_query.max_results}".encode()
        ).hexdigest()[:16]
        cache_path = self.cache_dir / f"arxiv-{key}.json"
        if cache_path.exists():
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            records = cached["records"] if isinstance(cached, dict) else cached
            traceability_fields = {"venue", "publication_type", "pdf_url"}
            if records and all(traceability_fields.issubset(item) for item in records):
                return [SourceRecord.model_validate(item) for item in records]
        params = {
            "search_query": _search_expression(watch_query.query),
            "start": 0,
            "max_results": watch_query.max_results,
            "sortBy": "submittedDate",
            "sortOrder": "descending",
        }
        global _last_request_at
        elapsed = time.monotonic() - _last_request_at
        if elapsed < 3.0:
            time.sleep(3.0 - elapsed)
        try:
            with httpx.Client(
                timeout=self.timeout_seconds,
                headers={"User-Agent": "ResearchRadar/0.1 local-demo"},
            ) as client:
                response = self._get_with_retries(client, self.endpoint, params=params)
        except httpx.HTTPError as exc:
            raise RuntimeError(f"arxiv_search_failed: {exc}") from exc
        finally:
            _last_request_at = time.monotonic()
        records = self._parse_records(response.text)
        if records:
            # An empty day is a valid answer, not a cacheable one: caching it
            # would hide papers published later the same day.
            cache_path.write_text(
                json.dumps(
                    {
                        "query": watch_query.model_dump(),
                        "fetched_at": datetime.now(timezone.utc).isoformat(),
                        "returned_ids": [record.external_id for record in records],
                        "records": [record.model_dump() for record in records],
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
        return records

    def lookup(self, arxiv_ids: list[str]) -> list[SourceRecord]:
        """Fetch canonical traceability metadata for known arXiv records."""

        unique_ids = list(dict.fromkeys(item for item in arxiv_ids if item))
        if not unique_ids:
            return []
        global _last_request_at
        elapsed = time.monotonic() - _last_request_at
        if elapsed < 3.0:
            time.sleep(3.0 - elapsed)
        try:
            with httpx.Client(
                timeout=self.timeout_seconds,
                headers={"User-Agent": "ResearchRadar/0.1 local-demo"},
            ) as client:
                response = self._get_with_retries(
                    client,
                    self.endpoint,
                    params={"id_list": ",".join(unique_ids), "max_results": len(unique_ids)},
                )
        except httpx.HTTPError as exc:
            raise RuntimeError(f"arxiv_lookup_failed: {exc}") from exc
        finally:
            _last_request_at = time.monotonic()
        return self._parse_records(response.text)

    def _parse_records(self, atom_text: str) -> list[SourceRecord]:
        feed = feedparser.parse(atom_text)
        records: list[SourceRecord] = []
        for entry in feed.entries:
            arxiv_id = entry.id.rsplit("/", 1)[-1]
            journal_ref = (entry.get("arxiv_journal_ref") or "").strip() or None
            links = entry.get("links", [])
            doi_link = next(
                (link.get("href") for link in links if link.get("title") == "doi"),
                None,
            )
            doi = self._normalize_doi(entry.get("arxiv_doi") or doi_link)
            abstract_url = next(
                (
                    link.get("href")
                    for link in links
                    if link.get("rel") == "alternate"
                ),
                None,
            ) or entry.link
            pdf_url = next(
                (
                    link.get("href")
                    for link in links
                    if link.get("type") == "application/pdf"
                    or link.get("title") == "pdf"
                ),
                None,
            ) or f"https://arxiv.org/pdf/{arxiv_id}"
            category = (
                entry.get("arxiv_primary_category") or {}
            ).get("term")
            venue = journal_ref or "arXiv"
            records.append(
                SourceRecord(
                    source_kind=self.source_kind,
                    external_id=f"arxiv:{arxiv_id}",
                    title=" ".join(entry.title.split()),
                    authors=[author.name for author in entry.get("authors", [])],
                    abstract=" ".join(entry.summary.split()),
                    url=abstract_url,
                    published_at=entry.get("published"),
                    arxiv_id=arxiv_id,
                    doi=doi,
                    license=entry.get("arxiv_license"),
                    venue=venue,
                    publication_type=(
                        "journal_article" if journal_ref else "other" if doi else "preprint"
                    ),
                    pdf_url=pdf_url,
                    arxiv_primary_category=category,
                    fields_of_study=[],
                    ccf_rank=ccf_rank_for_venue(venue),
                )
            )
        return records

    @staticmethod
    def _normalize_doi(value: str | None) -> str | None:
        if not value:
            return None
        normalized = value.strip()
        normalized = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", normalized, flags=re.I)
        normalized = re.sub(r"^doi:\s*", "", normalized, flags=re.I)
        return normalized or None

    def _text_cache_path(self, arxiv_id: str) -> Path:
        safe_id = re.sub(r"[^A-Za-z0-9._-]", "_", arxiv_id)
        return self.cache_dir / "full_text" / f"{safe_id}.txt"

    def cached_full_text(self, arxiv_id: str) -> str | None:
        """Return the complete cached PDF parse without any network request."""

        text_path = self._text_cache_path(arxiv_id)
        if not text_path.exists():
            return None
        return text_path.read_text(encoding="utf-8")

    def fetch_full_text(self, arxiv_id: str, *, max_chars: int = 800_000) -> str:
        """Download and parse a public arXiv PDF, with a persistent text cache.

        The cache always stores the complete parsed text; ``max_chars`` only
        truncates the returned value, so a cached entry is never mistaken for
        a truncated one.
        """

        text_path = self._text_cache_path(arxiv_id)
        text_path.parent.mkdir(parents=True, exist_ok=True)
        if text_path.exists():
            return text_path.read_text(encoding="utf-8")[:max_chars]

        global _last_pdf_request_at
        with _pdf_lock:
            wait = PDF_REQUEST_INTERVAL - (time.monotonic() - _last_pdf_request_at)
            if wait > 0:
                time.sleep(wait)
            _last_pdf_request_at = time.monotonic()
        try:
            with httpx.Client(
                timeout=max(self.timeout_seconds, 30.0),
                follow_redirects=True,
                headers={"User-Agent": "ResearchRadar/0.1 local-demo"},
            ) as client:
                response = self._get_with_retries(
                    client, f"https://arxiv.org/pdf/{arxiv_id}"
                )
                if len(response.content) > 30 * 1024 * 1024:
                    raise RuntimeError("arxiv_pdf_too_large")
        except httpx.HTTPError as exc:
            raise RuntimeError(f"arxiv_pdf_download_failed: {exc}") from exc

        try:
            reader = PdfReader(io.BytesIO(response.content))
            text = "\n\n".join(
                page_text
                for page in reader.pages
                if (page_text := (page.extract_text() or "").strip())
            )
        except Exception as exc:
            raise RuntimeError(f"arxiv_pdf_parse_failed: {exc}") from exc
        if len(text) < 500:
            raise RuntimeError("arxiv_pdf_has_insufficient_text")
        text_path.write_text(text, encoding="utf-8")
        return text[:max_chars]
