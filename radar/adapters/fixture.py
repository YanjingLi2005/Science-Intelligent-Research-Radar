"""Offline-first fixture search adapter."""

import json
from pathlib import Path

from radar.schemas import SourceRecord, WatchQuery


class FixtureSearchAdapter:
    def __init__(self, fixture_dir: Path):
        self.fixture_dir = Path(fixture_dir)

    def search(self, case_id: str, watch_query: WatchQuery) -> list[SourceRecord]:
        query_terms = {term.lower() for term in watch_query.query.split() if len(term) > 2}
        records = []
        sources = json.loads((self.fixture_dir / "sources.json").read_text(encoding="utf-8"))
        for item in sources:
            abstract = (self.fixture_dir / item["content_file"]).read_text(encoding="utf-8").strip()
            haystack = f"{item['title']} {abstract}".lower()
            if not query_terms or any(term in haystack for term in query_terms):
                records.append(
                    SourceRecord(
                        external_id=item["external_id"], title=item["title"],
                        authors=item["authors"], abstract=abstract, url=item["url"],
                        published_at=item.get("published_at"), doi=item.get("doi"),
                        arxiv_id=item.get("arxiv_id"), license=item.get("license"),
                    )
                )
        return records[: watch_query.max_results]

