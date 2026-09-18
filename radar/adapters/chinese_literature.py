"""OpenAlex-backed search adapter for Chinese-language literature."""

from radar.schemas import SourceRecord, WatchQuery
from radar.adapters.openalex import OpenAlexSearchAdapter


class ChineseLiteratureSearchAdapter(OpenAlexSearchAdapter):
    """Search OpenAlex for Chinese-language articles and preprints."""

    source_kind = "chinese_literature"

    def search(self, case_id: str, watch_query: WatchQuery) -> list[SourceRecord]:
        del case_id  # OpenAlex is global; case scoping is handled by the caller.
        payload = self._get_works(
            {
                "search": watch_query.query,
                "filter": "language:zh,type:article|preprint",
                "per-page": min(watch_query.max_results, 50),
            },
            error_label="chinese_literature_search_failed",
        )
        return self._parse_records(payload)
