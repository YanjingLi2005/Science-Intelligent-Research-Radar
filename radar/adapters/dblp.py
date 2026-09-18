"""Small DBLP bibliographic search adapter used by reference validity checks."""

from __future__ import annotations

import re

import httpx

from radar.schemas import ReferenceMatch


def _normalize_title(value: str) -> str:
    return " ".join(
        "".join(character.lower() if character.isalnum() else " " for character in value).split()
    )


def title_similarity(query_title: str, candidate_title: str) -> float:
    """Return deterministic token-Jaccard similarity, with containment as exact."""

    query = _normalize_title(query_title)
    candidate = _normalize_title(candidate_title)
    if not query or not candidate:
        return 0.0
    if query == candidate or query in candidate or candidate in query:
        return 1.0
    query_tokens = set(query.split())
    candidate_tokens = set(candidate.split())
    return len(query_tokens & candidate_tokens) / max(len(query_tokens | candidate_tokens), 1)


_title_similarity = title_similarity


def _as_list(value) -> list:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _author_names(value) -> list[str]:
    names: list[str] = []
    for author in _as_list(value):
        if isinstance(author, dict):
            author = author.get("text") or author.get("name") or author.get("#text")
        name = str(author or "").strip()
        if name:
            names.append(name)
    return names


def _year(value) -> int | None:
    match = re.search(r"\b(?:18|19|20|21)\d{2}\b", str(value or ""))
    return int(match.group(0)) if match else None


def _arxiv_id(value) -> str | None:
    match = re.search(
        r"(?i)(?:arxiv:\s*|https?://arxiv\.org/(?:abs|pdf)/)?"
        r"((?:\d{4}\.\d{4,5}(?:v\d+)?)|(?:[a-z][a-z-]+(?:\.[A-Z]{2})?/\d{7}))",
        str(value or ""),
    )
    return match.group(1).rstrip(".,;:)") if match else None


class DblpSearchAdapter:
    source_kind = "dblp"
    endpoint = "https://dblp.org/search/publ/api"

    def __init__(self, timeout_seconds: float = 30.0):
        self.timeout_seconds = timeout_seconds

    def _request(self, query: str, max_results: int) -> dict:
        try:
            with httpx.Client(
                timeout=self.timeout_seconds,
                headers={"User-Agent": "ResearchRadar/0.1 local-demo"},
            ) as client:
                response = client.get(
                    self.endpoint,
                    params={"q": query, "format": "json", "h": max_results},
                )
                response.raise_for_status()
                payload = response.json()
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            raise RuntimeError(f"dblp_search_failed: {exc}") from exc
        if not isinstance(payload, dict):
            raise RuntimeError("dblp_search_failed: response was not an object")
        return payload

    @staticmethod
    def _hits(payload: dict) -> list[dict]:
        result = payload.get("result") or {}
        hits = result.get("hits") or {}
        items = hits.get("hit") or []
        return _as_list(items)

    def _parse_matches(self, payload: dict) -> list[ReferenceMatch]:
        matches: list[ReferenceMatch] = []
        for hit in self._hits(payload):
            if not isinstance(hit, dict):
                continue
            info = hit.get("info") or {}
            title = " ".join(str(info.get("title") or "").split()).rstrip(".")
            if not title:
                continue
            key = str(info.get("key") or hit.get("@id") or "").strip()
            external_id = f"dblp:{key}" if key else f"dblp:{_normalize_title(title)}"
            record_url = str(info.get("url") or "").strip()
            if "dblp.org" not in record_url.lower():
                record_url = f"https://dblp.org/rec/{key}" if key else "https://dblp.org"
            doi = str(info.get("doi") or "").strip() or None
            if doi:
                doi = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", doi, flags=re.I).rstrip(".,;:)")
            ee = info.get("ee")
            arxiv_id = _arxiv_id(ee)
            matches.append(
                ReferenceMatch(
                    source_kind=self.source_kind,
                    external_id=external_id,
                    title=title,
                    authors=_author_names((info.get("authors") or {}).get("author") if isinstance(info.get("authors"), dict) else info.get("authors")),
                    year=_year(info.get("year")),
                    venue=str(info.get("venue") or "").strip() or None,
                    doi=doi,
                    arxiv_id=arxiv_id,
                    url=record_url,
                )
            )
        return matches

    def search(self, query: str, max_results: int = 10) -> list[ReferenceMatch]:
        payload = self._request(query, max(1, min(max_results, 100)))
        try:
            return self._parse_matches(payload)
        except (AttributeError, KeyError, TypeError, ValueError) as exc:
            raise RuntimeError(f"dblp_search_failed: {exc}") from exc

    def resolve_by_title(self, title: str) -> ReferenceMatch | None:
        candidates = self.search(title, max_results=5)
        matching = [candidate for candidate in candidates if title_similarity(title, candidate.title) >= 0.6]
        if not matching:
            return None
        return max(matching, key=lambda candidate: title_similarity(title, candidate.title))
