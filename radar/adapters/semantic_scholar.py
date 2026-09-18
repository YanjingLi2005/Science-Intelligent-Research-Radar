"""Semantic Scholar search adapter for citation-aware literature discovery."""

import re
import time

import httpx

from radar.schemas import SourceRecord, WatchQuery
from radar.venue_ranks import ccf_rank_for_venue


MAX_RETRIES = 3


def _normalize_doi(value: str | None) -> str | None:
    if not value:
        return None
    normalized = value.strip()
    normalized = re.sub(
        r"^https?://(?:dx\.)?doi\.org/", "", normalized, flags=re.IGNORECASE
    )
    return normalized or None


def _publication_type(item: dict, *, arxiv_id: str | None) -> str:
    types = {str(value).lower() for value in (item.get("publicationTypes") or [])}
    if "conference" in types or "conferencepaper" in types:
        return "conference_paper"
    if "journalarticle" in types or "review" in types:
        return "journal_article"
    return "preprint" if arxiv_id else "other"


class SemanticScholarAdapter:
    """Query Semantic Scholar's public Graph API with bounded retries."""

    endpoint = "https://api.semanticscholar.org/graph/v1/paper/search"
    source_kind = "semantic_scholar"
    paper_fields = (
        "title,abstract,authors,year,publicationDate,externalIds,url,"
        "openAccessPdf,venue,publicationTypes,citationCount,fieldsOfStudy"
    )

    def __init__(
        self,
        *,
        timeout_seconds: float = 30.0,
        api_key: str | None = None,
        max_retries: int = MAX_RETRIES,
    ):
        self.timeout_seconds = timeout_seconds
        self.api_key = api_key
        self.max_retries = max_retries

    def search(self, case_id: str, watch_query: WatchQuery) -> list[SourceRecord]:
        del case_id  # The API is global; case scoping is handled by the caller.
        params = {
            "query": watch_query.query,
            "limit": min(watch_query.max_results, 100),
            "fields": self.paper_fields,
        }
        headers = {"User-Agent": "ResearchRadar/0.3"}
        if self.api_key:
            headers["x-api-key"] = self.api_key

        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                with httpx.Client(timeout=self.timeout_seconds, headers=headers) as client:
                    response = client.get(self.endpoint, params=params)
                if response.status_code == 429 or response.status_code >= 500:
                    response.raise_for_status()
                response.raise_for_status()
                payload = response.json()
                return self._map_records(payload.get("data") or [])[: watch_query.max_results]
            except (httpx.HTTPError, ValueError, TypeError) as exc:
                last_error = exc
                if attempt >= self.max_retries:
                    break
                time.sleep(min(2 ** attempt, 8))

        raise RuntimeError(f"semantic_scholar_search_failed: {last_error}")

    def get_references(
        self, paper_id: str, max_results: int = 20
    ) -> list[SourceRecord]:
        """Return papers referenced by a Semantic Scholar paper."""

        return self._get_graph_records(
            paper_id,
            relation="references",
            paper_key="citedPaper",
            max_results=max_results,
        )

    def get_citations(
        self, paper_id: str, max_results: int = 20
    ) -> list[SourceRecord]:
        """Return papers citing a Semantic Scholar paper."""

        return self._get_graph_records(
            paper_id,
            relation="citations",
            paper_key="citingPaper",
            max_results=max_results,
        )

    def _get_graph_records(
        self,
        paper_id: str,
        *,
        relation: str,
        paper_key: str,
        max_results: int,
    ) -> list[SourceRecord]:
        endpoint = f"https://api.semanticscholar.org/graph/v1/paper/{paper_id}/{relation}"
        params = {
            "limit": min(max_results, 100),
            "fields": self.paper_fields,
        }
        headers = {"User-Agent": "ResearchRadar/0.3"}
        if self.api_key:
            headers["x-api-key"] = self.api_key

        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                with httpx.Client(timeout=self.timeout_seconds, headers=headers) as client:
                    response = client.get(endpoint, params=params)
                if response.status_code == 429 or response.status_code >= 500:
                    response.raise_for_status()
                response.raise_for_status()
                payload = response.json()
                items = [
                    entry[paper_key]
                    for entry in (payload.get("data") or [])
                    if isinstance(entry, dict) and isinstance(entry.get(paper_key), dict)
                ]
                return self._map_records(items)[:max_results]
            except (httpx.HTTPError, ValueError, TypeError) as exc:
                last_error = exc
                if attempt >= self.max_retries:
                    break
                time.sleep(min(2 ** attempt, 8))

        raise RuntimeError(f"semantic_scholar_{relation}_failed: {last_error}")

    def _map_records(self, items: list[dict]) -> list[SourceRecord]:
        records: list[SourceRecord] = []
        for item in items:
            paper_id = str(item.get("paperId") or "").strip()
            title = " ".join(str(item.get("title") or "").split())
            if not paper_id or not title:
                continue
            external_ids = item.get("externalIds") or {}
            doi = _normalize_doi(external_ids.get("DOI"))
            arxiv_id = str(external_ids.get("ArXiv") or "").strip() or None
            authors = [
                str(author.get("name") or "").strip()
                for author in (item.get("authors") or [])
                if str(author.get("name") or "").strip()
            ]
            published_at = item.get("publicationDate")
            if not published_at and item.get("year"):
                published_at = f"{item['year']}-01-01"
            oa_pdf = item.get("openAccessPdf") or {}
            url = str(item.get("url") or "").strip()
            url = url or f"https://www.semanticscholar.org/paper/{paper_id}"
            venue = str(item.get("venue") or "").strip() or None
            records.append(
                SourceRecord(
                    source_kind=self.source_kind,
                    external_id=f"semantic_scholar:{paper_id}",
                    title=title,
                    authors=authors,
                    abstract=str(item.get("abstract") or "").strip(),
                    url=url,
                    published_at=published_at,
                    doi=doi,
                    arxiv_id=arxiv_id,
                    venue=venue,
                    publication_type=_publication_type(item, arxiv_id=arxiv_id),
                    pdf_url=str(oa_pdf.get("url") or "").strip() or None,
                    cited_by_count=item.get("citationCount"),
                    arxiv_primary_category=None,
                    fields_of_study=[str(v) for v in (item.get("fieldsOfStudy") or []) if v],
                    ccf_rank=ccf_rank_for_venue(venue),
                )
            )
        return records
