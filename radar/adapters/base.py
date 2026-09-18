"""Search adapter protocol."""

from typing import Protocol

from radar.schemas import SourceRecord, WatchQuery


class SearchAdapter(Protocol):
    def search(self, case_id: str, watch_query: WatchQuery) -> list[SourceRecord]: ...

