"""Optional OpenAlex search adapter (second public literature source)."""

import re
import time
from datetime import date, timedelta

import httpx

from radar.config import get_settings
from radar.schemas import SourceRecord, WatchQuery
from radar.venue_ranks import ccf_rank_for_venue


MAX_RETRIES = 3
DEFAULT_LOOKBACK_DAYS = 30
# OpenAlex topic-classification field ids used to scope keyword search to the
# product's focus disciplines; fields/17 is Computer Science. Multiple ids are
# OR-ed. Pass topic_field_ids=None (or ()) to disable discipline filtering.
DEFAULT_TOPIC_FIELD_IDS: tuple[str, ...] = ("17",)

_PUBLICATION_TYPES = {
    "article": "journal_article",
    "preprint": "preprint",
}


def _abstract_from_inverted_index(inverted_index: dict | None) -> str:
    """Rebuild plain abstract text from OpenAlex's positional token index."""

    if not inverted_index:
        return ""
    positions: dict[int, str] = {}
    for word, places in inverted_index.items():
        for place in places:
            positions[place] = word
    return " ".join(positions[index] for index in sorted(positions))


def _normalize_doi(value: str | None) -> str | None:
    if not value:
        return None
    normalized = value.strip()
    normalized = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", normalized, flags=re.I)
    normalized = re.sub(r"^doi:\s*", "", normalized, flags=re.I)
    return normalized or None


def _normalize_title(value: str) -> str:
    return " ".join(
        "".join(character.lower() if character.isalnum() else " " for character in value).split()
    )


def _titles_match(query_title: str, candidate_title: str) -> bool:
    """Guard against anchoring the citation graph to an unrelated work.

    OpenAlex search ranks by relevance, not identity, so the first hit is
    only accepted on exact/contained normalized titles or a high token
    overlap (Jaccard >= 0.6).
    """

    query = _normalize_title(query_title)
    candidate = _normalize_title(candidate_title)
    if not query or not candidate:
        return False
    if query == candidate or query in candidate or candidate in query:
        return True
    query_tokens = set(query.split())
    candidate_tokens = set(candidate.split())
    union = query_tokens | candidate_tokens
    return len(query_tokens & candidate_tokens) / max(len(union), 1) >= 0.6


class OpenAlexSearchAdapter:
    source_kind = "openalex"
    endpoint = "https://api.openalex.org/works"

    def __init__(
        self,
        timeout_seconds: float = 30.0,
        *,
        mailto: str | None = None,
        lookback_days: int = DEFAULT_LOOKBACK_DAYS,
        topic_field_ids: tuple[str, ...] | None = DEFAULT_TOPIC_FIELD_IDS,
    ):
        self.timeout_seconds = timeout_seconds
        self.mailto = mailto or get_settings().crossref_mailto
        self.lookback_days = lookback_days
        self.topic_field_ids = tuple(topic_field_ids or ())

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
                time.sleep(delay)
                delay *= 2
            except (httpx.TimeoutException, httpx.TransportError):
                if attempt == MAX_RETRIES:
                    raise
                time.sleep(delay)
                delay *= 2
        raise AssertionError("unreachable")

    def search(self, case_id: str, watch_query: WatchQuery) -> list[SourceRecord]:
        since = date.today() - timedelta(days=self.lookback_days)
        filters = [
            f"from_publication_date:{since.isoformat()}",
            "type:article|preprint",
        ]
        if self.topic_field_ids:
            # Keyword search is the noisy path: without a discipline filter a
            # query like "transformer translation" also returns medical or
            # power-grid papers that merely mention the keywords. The
            # citation-graph path (get_citing_works) stays unfiltered on
            # purpose — citing works are already anchored to the manuscript's
            # own references, and cross-discipline citations are signal there.
            filters.append(
                "primary_topic.field.id:" + "|".join(self.topic_field_ids)
            )
        params = {
            "search": watch_query.query,
            "filter": ",".join(filters),
            "sort": "publication_date:desc",
            "per-page": min(watch_query.max_results, 200),
            "mailto": self.mailto,
        }
        try:
            with httpx.Client(
                timeout=self.timeout_seconds,
                headers={"User-Agent": "ResearchRadar/0.1 local-demo"},
            ) as client:
                response = self._get_with_retries(client, self.endpoint, params=params)
        except httpx.HTTPError as exc:
            raise RuntimeError(f"openalex_search_failed: {exc}") from exc
        return self._parse_records(response.json())

    def _get_works(self, params: dict, *, error_label: str) -> dict:
        """GET the works endpoint with retries and return the decoded payload."""

        params = {**params, "mailto": self.mailto}
        try:
            with httpx.Client(
                timeout=self.timeout_seconds,
                headers={"User-Agent": "ResearchRadar/0.1 local-demo"},
            ) as client:
                response = self._get_with_retries(client, self.endpoint, params=params)
        except httpx.HTTPError as exc:
            raise RuntimeError(f"{error_label}: {exc}") from exc
        return response.json()

    def get_work_by_doi(self, doi: str) -> SourceRecord | None:
        """Look up a single work by DOI and return it as a SourceRecord."""
        normalized = _normalize_doi(doi)
        if not normalized:
            return None
        payload = self._get_works(
            {"filter": f"doi:{normalized}", "per-page": 1},
            error_label="openalex_doi_lookup_failed",
        )
        records = self._parse_records(payload)
        return records[0] if records else None

    def get_work_by_title(self, title: str) -> SourceRecord | None:
        """Return the first OpenAlex work whose title closely matches ``title``."""
        if not title.strip():
            return None
        payload = self._get_works(
            {"search": title, "per-page": 3},
            error_label="openalex_title_lookup_failed",
        )
        for record in self._parse_records(payload):
            if _titles_match(title, record.title):
                return record
        return None

    def resolve_work(self, title: str) -> str | None:
        """Resolve a reference title to its OpenAlex work id, or None.

        The first search hit is accepted only when its title closely matches
        (see ``_titles_match``) so a loose result can never anchor citation
        expansion to an unrelated paper.
        """

        payload = self._get_works(
            {"search": title, "per-page": 1}, error_label="openalex_resolve_failed"
        )
        results = payload.get("results", [])
        if not results:
            return None
        work = results[0]
        candidate = (work.get("title") or work.get("display_name") or "").strip()
        if not _titles_match(title, candidate):
            return None
        return (work.get("id") or "").rsplit("/", 1)[-1] or None

    def get_citing_works(
        self, openalex_id: str, *, since_days: int = 365, max_results: int = 5
    ) -> list[SourceRecord]:
        """Return recent works that cite the given OpenAlex work."""

        work_id = openalex_id.rsplit("/", 1)[-1]
        since = date.today() - timedelta(days=since_days)
        payload = self._get_works(
            {
                "filter": f"cites:{work_id},from_publication_date:{since.isoformat()}",
                "sort": "publication_date:desc",
                "per-page": min(max_results, 200),
            },
            error_label="openalex_citing_works_failed",
        )
        return self._parse_records(payload)

    def _parse_records(self, payload: dict) -> list[SourceRecord]:
        records: list[SourceRecord] = []
        for work in payload.get("results", []):
            openalex_id = (work.get("id") or "").rsplit("/", 1)[-1]
            title = (work.get("title") or work.get("display_name") or "").strip()
            if not openalex_id or not title:
                continue
            primary_location = work.get("primary_location") or {}
            best_oa_location = work.get("best_oa_location") or {}
            source = primary_location.get("source") or {}
            work_type = work.get("type")
            venue = source.get("display_name")
            concepts = [
                concept.get("display_name")
                for concept in (work.get("concepts") or [])
                if concept.get("display_name")
            ]
            primary_topic = work.get("primary_topic") or {}
            topic_name = primary_topic.get("display_name") or primary_topic.get("name")
            if topic_name and topic_name not in concepts:
                concepts.insert(0, topic_name)
            records.append(
                SourceRecord(
                    source_kind=self.source_kind,
                    external_id=f"openalex:{openalex_id}",
                    title=title,
                    authors=[
                        authorship.get("author", {}).get("display_name")
                        for authorship in work.get("authorships", [])
                        if authorship.get("author", {}).get("display_name")
                    ],
                    abstract=_abstract_from_inverted_index(
                        work.get("abstract_inverted_index")
                    ),
                    url=primary_location.get("landing_page_url") or work.get("id"),
                    published_at=work.get("publication_date"),
                    doi=_normalize_doi(work.get("doi")),
                    venue=venue,
                    publication_type=_PUBLICATION_TYPES.get(work_type, "other"),
                    pdf_url=best_oa_location.get("pdf_url"),
                    cited_by_count=work.get("cited_by_count"),
                    arxiv_primary_category=None,
                    fields_of_study=concepts[:8],
                    ccf_rank=ccf_rank_for_venue(venue),
                )
            )
        return records
