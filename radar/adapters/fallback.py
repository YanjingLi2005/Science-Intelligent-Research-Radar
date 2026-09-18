"""Explicit live-to-fixture degradation wrapper."""

from radar.adapters.base import SearchAdapter
from radar.schemas import SourceRecord, WatchQuery


class FallbackSearchAdapter:
    def __init__(self, primary: SearchAdapter, fallback: SearchAdapter):
        self.primary = primary
        self.fallback = fallback
        self.last_error: str | None = None
        self.used_fallback = False

    def search(self, case_id: str, watch_query: WatchQuery) -> list[SourceRecord]:
        try:
            records = self.primary.search(case_id, watch_query)
            self.used_fallback = False
            self.last_error = None
            return records
        except Exception as exc:
            self.used_fallback = True
            self.last_error = str(exc)
            return self.fallback.search(case_id, watch_query)

