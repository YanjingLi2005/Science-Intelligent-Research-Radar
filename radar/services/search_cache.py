"""Local TTL response caching for external academic search APIs (arXiv, Semantic Scholar, CrossRef, etc.)."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from radar.config import get_settings
from radar.schemas import SourceRecord, WatchQuery

logger = logging.getLogger("radar.search_cache")

DEFAULT_TTL_SECONDS = 86400  # 24 hours

EXTERNAL_ACADEMIC_SOURCES = {
    "arxiv",
    "semantic_scholar",
    "crossref",
    "crossref_search",
    "openalex",
    "pubmed",
    "dblp",
    "chinese_literature",
    "custom",
}


def compute_query_hash(query: str) -> str:
    """Generate a stable 16-character hash from a normalized search query."""
    normalized = " ".join(query.strip().lower().split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


def compute_response_hash(records: list[SourceRecord]) -> str:
    """Compute the canonical deterministic SHA-256 fingerprint matching retrieval_receipts."""
    payload = [
        item.model_dump() if hasattr(item, "model_dump") else str(item)
        for item in records
    ]
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


@dataclass
class CacheEntry:
    query: str
    query_hash: str
    source_type: str
    page: int
    cached_at: float
    ttl_seconds: int
    response_hash: str
    records: list[dict[str, Any]]

    @property
    def is_expired(self) -> bool:
        return (time.time() - self.cached_at) > self.ttl_seconds

    def to_source_records(self) -> list[SourceRecord]:
        return [SourceRecord.model_validate(r) for r in self.records]


class AcademicSearchCache:
    """Local TTL cache for external academic literature search responses."""

    def __init__(
        self,
        cache_dir: Path | str | None = None,
        default_ttl_seconds: int = DEFAULT_TTL_SECONDS,
        *,
        enabled: bool = True,
        cache_empty: bool = False,
        allowed_sources: set[str] | None = None,
    ):
        self.enabled = enabled
        self.default_ttl_seconds = default_ttl_seconds
        self.cache_empty = cache_empty
        self.allowed_sources = (
            {s.lower() for s in allowed_sources}
            if allowed_sources is not None
            else set(EXTERNAL_ACADEMIC_SOURCES)
        )
        if cache_dir is not None:
            self.cache_dir = Path(cache_dir)
        else:
            self.cache_dir = get_settings().data_dir / "cache" / "external_search"

        self._memory_cache: dict[str, CacheEntry] = {}
        self.hits = 0
        self.misses = 0
        self.stores = 0
        self.evictions = 0

    def _ensure_dir(self) -> None:
        if self.cache_dir:
            self.cache_dir.mkdir(parents=True, exist_ok=True)

    def cache_key(self, query: str, source_type: str, page: int = 1) -> str:
        """Construct composite cache key: source_type + query_hash + page."""
        q_hash = compute_query_hash(query)
        s_type = source_type.strip().lower()
        return f"{s_type}_{q_hash}_p{page}"

    def _file_path(self, key: str) -> Path:
        return self.cache_dir / f"search_{key}.json"

    def _is_source_allowed(self, source_type: str) -> bool:
        s = source_type.strip().lower()
        if self.allowed_sources is not None:
            return s in self.allowed_sources
        return True

    def get(
        self,
        query: str,
        source_type: str,
        page: int = 1,
    ) -> tuple[list[SourceRecord], str] | None:
        """Retrieve cached search records and response_hash if present and fresh."""
        if not self.enabled:
            return None
        if not self._is_source_allowed(source_type):
            return None

        key = self.cache_key(query, source_type, page)

        # 1. Check memory cache
        entry = self._memory_cache.get(key)
        if entry is not None:
            if not entry.is_expired:
                self.hits += 1
                return entry.to_source_records(), entry.response_hash
            else:
                self._memory_cache.pop(key, None)
                self.evictions += 1

        # 2. Check disk cache
        file_path = self._file_path(key)
        if file_path.exists():
            try:
                data = json.loads(file_path.read_text(encoding="utf-8"))
                entry = CacheEntry(
                    query=data["query"],
                    query_hash=data["query_hash"],
                    source_type=data["source_type"],
                    page=data.get("page", 1),
                    cached_at=data["cached_at"],
                    ttl_seconds=data.get("ttl_seconds", self.default_ttl_seconds),
                    response_hash=data["response_hash"],
                    records=data.get("records", []),
                )
                if not entry.is_expired:
                    self._memory_cache[key] = entry
                    self.hits += 1
                    return entry.to_source_records(), entry.response_hash
                else:
                    file_path.unlink(missing_ok=True)
                    self.evictions += 1
            except Exception as exc:
                logger.warning("Failed to read search cache %s: %s", file_path, exc)
                file_path.unlink(missing_ok=True)

        self.misses += 1
        return None

    def set(
        self,
        query: str,
        source_type: str,
        records: list[SourceRecord],
        page: int = 1,
        ttl_seconds: int | None = None,
    ) -> str:
        """Cache a search result and return its canonical response_hash."""
        response_hash = compute_response_hash(records)
        if not self.enabled:
            return response_hash
        if not records and not self.cache_empty:
            return response_hash
        if not self._is_source_allowed(source_type):
            return response_hash

        ttl = ttl_seconds if ttl_seconds is not None else self.default_ttl_seconds
        q_hash = compute_query_hash(query)
        key = self.cache_key(query, source_type, page)
        raw_records = [
            r.model_dump() if hasattr(r, "model_dump") else dict(r)
            for r in records
        ]

        entry = CacheEntry(
            query=query,
            query_hash=q_hash,
            source_type=source_type.strip().lower(),
            page=page,
            cached_at=time.time(),
            ttl_seconds=ttl,
            response_hash=response_hash,
            records=raw_records,
        )

        self._memory_cache[key] = entry

        try:
            self._ensure_dir()
            payload = {
                "query": entry.query,
                "query_hash": entry.query_hash,
                "source_type": entry.source_type,
                "page": entry.page,
                "cached_at": entry.cached_at,
                "ttl_seconds": entry.ttl_seconds,
                "response_hash": entry.response_hash,
                "records": entry.records,
            }
            tmp_file = self.cache_dir / f"search_{key}.tmp.{os.getpid()}"
            tmp_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp_file.replace(self._file_path(key))
            self.stores += 1
        except Exception as exc:
            logger.warning("Failed to persist search cache for %s: %s", key, exc)

        return response_hash

    def invalidate(self, query: str | None = None, source_type: str | None = None) -> int:
        """Invalidate entries matching query and/or source_type."""
        count = 0
        s_type = source_type.strip().lower() if source_type else None
        q_hash = compute_query_hash(query) if query else None

        # Memory eviction
        to_del = []
        for k, v in self._memory_cache.items():
            if s_type and v.source_type != s_type:
                continue
            if q_hash and v.query_hash != q_hash:
                continue
            to_del.append(k)
        for k in to_del:
            self._memory_cache.pop(k, None)
            count += 1

        # Disk eviction
        if self.cache_dir.exists():
            for f in self.cache_dir.glob("search_*.json"):
                try:
                    data = json.loads(f.read_text(encoding="utf-8"))
                    if s_type and data.get("source_type") != s_type:
                        continue
                    if q_hash and data.get("query_hash") != q_hash:
                        continue
                    f.unlink(missing_ok=True)
                    count += 1
                except Exception:
                    f.unlink(missing_ok=True)

        return count

    def clear(self) -> int:
        """Clear all cached search entries."""
        count = len(self._memory_cache)
        self._memory_cache.clear()
        if self.cache_dir.exists():
            for f in self.cache_dir.glob("search_*.json"):
                f.unlink(missing_ok=True)
                count += 1
        return count

    def stats(self) -> dict[str, Any]:
        """Return cache runtime telemetry."""
        total = self.hits + self.misses
        hit_ratio = (self.hits / total) if total > 0 else 0.0
        return {
            "hits": self.hits,
            "misses": self.misses,
            "stores": self.stores,
            "evictions": self.evictions,
            "total_requests": total,
            "hit_ratio": round(hit_ratio, 4),
            "memory_entries": len(self._memory_cache),
        }

    def wrap_adapter(self, adapter: Any, source_type: str | None = None) -> CachedSearchAdapter:
        """Wrap a SearchAdapter instance with automatic caching."""
        s_type = source_type or getattr(adapter, "source_kind", adapter.__class__.__name__.lower())
        return CachedSearchAdapter(adapter=adapter, cache=self, source_type=s_type)


class CachedSearchAdapter:
    """Transparent proxy that adds caching around any SearchAdapter."""

    def __init__(
        self,
        adapter: Any,
        cache: AcademicSearchCache,
        source_type: str,
    ):
        self.adapter = adapter
        self.cache = cache
        self.source_type = source_type

    @property
    def source_kind(self) -> str:
        return getattr(self.adapter, "source_kind", self.source_type)

    @property
    def endpoint(self) -> str:
        return getattr(self.adapter, "endpoint", self.adapter.__class__.__name__)

    def search(self, case_id: str, watch_query: WatchQuery, page: int = 1) -> list[SourceRecord]:
        cached = self.cache.get(watch_query.query, self.source_type, page=page)
        if cached is not None:
            records, _ = cached
            return records[:watch_query.max_results]

        records = self.adapter.search(case_id, watch_query)
        self.cache.set(watch_query.query, self.source_type, records, page=page)
        return records

    def __getattr__(self, name: str) -> Any:
        return getattr(self.adapter, name)
