"""Unit tests for AcademicSearchCache and RetrievalService external search caching."""

from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from radar.models import RetrievalReceipt
from radar.schemas import SourceRecord, WatchQuery
from radar.services.retrieval_service import (
    AcademicSearchCache,
    CachedSearchAdapter,
    RetrievalService,
    compute_query_hash,
    compute_response_hash,
)


def _make_dummy_record(title: str, doi: str = "10.1234/test.1") -> SourceRecord:
    return SourceRecord(
        source_kind="arxiv",
        external_id=f"arxiv:{doi}",
        title=title,
        authors=["Alice Smith", "Bob Jones"],
        abstract="This is a test abstract discussing machine learning.",
        venue="ICML 2026",
        doi=doi,
        url=f"https://doi.org/{doi}",
        published_at="2026-06-15",
    )


def test_query_hash_normalization():
    """Verify query hash is lowercase and whitespace-normalized."""
    h1 = compute_query_hash("Deep Learning   Transformers ")
    h2 = compute_query_hash("deep learning transformers")
    assert h1 == h2
    assert len(h1) == 16


def test_cache_miss_then_hit(tmp_path: Path):
    """Verify cache miss on first call followed by cache hit on second call."""
    cache = AcademicSearchCache(cache_dir=tmp_path / "search_cache", default_ttl_seconds=3600)
    retrieval = RetrievalService(search_cache=cache)

    mock_adapter = MagicMock()
    mock_adapter.source_kind = "arxiv"
    dummy_records = [_make_dummy_record("Attention is All You Need")]
    mock_adapter.search.return_value = dummy_records

    watch_query = WatchQuery(query="transformers attention", max_results=10)

    # 1. First call -> Cache Miss
    results, is_hit, hash1 = retrieval.search_external(mock_adapter, "case-1", watch_query, page=1)
    assert is_hit is False
    assert len(results) == 1
    assert results[0].title == "Attention is All You Need"
    assert mock_adapter.search.call_count == 1
    assert cache.stats()["misses"] == 1
    assert cache.stats()["hits"] == 0
    assert cache.stats()["stores"] == 1

    # 2. Second call with same query -> Cache Hit
    results2, is_hit2, hash2 = retrieval.search_external(mock_adapter, "case-1", watch_query, page=1)
    assert is_hit2 is True
    assert len(results2) == 1
    assert results2[0].title == "Attention is All You Need"
    assert hash1 == hash2
    # Adapter must NOT have been called again
    assert mock_adapter.search.call_count == 1
    assert cache.stats()["hits"] == 1
    assert cache.stats()["misses"] == 1


def test_cache_expiration(tmp_path: Path):
    """Verify expired cache entries trigger a cache miss and re-fetch."""
    cache = AcademicSearchCache(cache_dir=tmp_path / "search_cache", default_ttl_seconds=1)
    retrieval = RetrievalService(search_cache=cache)

    mock_adapter = MagicMock()
    mock_adapter.source_kind = "semantic_scholar"
    mock_adapter.search.side_effect = [
        [_make_dummy_record("Paper Version 1")],
        [_make_dummy_record("Paper Version 2")],
    ]

    watch_query = WatchQuery(query="graph neural networks", max_results=5)

    # First fetch
    results1, is_hit1, _ = retrieval.search_external(mock_adapter, "case-1", watch_query, page=1)
    assert is_hit1 is False
    assert results1[0].title == "Paper Version 1"

    # Wait for TTL expiration
    time.sleep(1.1)

    # Second fetch should expire and re-call adapter
    results2, is_hit2, _ = retrieval.search_external(mock_adapter, "case-1", watch_query, page=1)
    assert is_hit2 is False
    assert results2[0].title == "Paper Version 2"
    assert mock_adapter.search.call_count == 2
    assert cache.stats()["evictions"] >= 1


def test_response_hash_consistency():
    """Verify response_hash computed by cache matches retrieval_receipts format."""
    records = [_make_dummy_record("Quantum Computing"), _make_dummy_record("Error Correction", doi="10.1234/qc.2")]
    hash1 = compute_response_hash(records)
    hash2 = compute_response_hash(records)
    assert hash1 == hash2
    assert len(hash1) == 64  # SHA-256 hex string


def test_source_and_page_partitioning(tmp_path: Path):
    """Verify different source types or pages for identical queries have isolated cache entries."""
    cache = AcademicSearchCache(cache_dir=tmp_path / "search_cache")

    records_arxiv = [_make_dummy_record("arXiv Paper")]
    records_s2 = [_make_dummy_record("Semantic Scholar Paper")]
    records_p2 = [_make_dummy_record("arXiv Paper Page 2")]

    cache.set("diffusion models", "arxiv", records_arxiv, page=1)
    cache.set("diffusion models", "semantic_scholar", records_s2, page=1)
    cache.set("diffusion models", "arxiv", records_p2, page=2)

    hit_arxiv, _ = cache.get("diffusion models", "arxiv", page=1)
    hit_s2, _ = cache.get("diffusion models", "semantic_scholar", page=1)
    hit_p2, _ = cache.get("diffusion models", "arxiv", page=2)

    assert hit_arxiv[0].title == "arXiv Paper"
    assert hit_s2[0].title == "Semantic Scholar Paper"
    assert hit_p2[0].title == "arXiv Paper Page 2"


def test_cached_search_adapter_wrapper(tmp_path: Path):
    """Verify CachedSearchAdapter proxy wraps any SearchAdapter conforming to protocol."""
    cache = AcademicSearchCache(cache_dir=tmp_path / "search_cache")
    mock_adapter = MagicMock()
    mock_adapter.source_kind = "crossref"
    mock_adapter.search.return_value = [_make_dummy_record("Crossref Discovered Paper")]

    wrapped = CachedSearchAdapter(adapter=mock_adapter, cache=cache, source_type="crossref")
    assert wrapped.source_kind == "crossref"

    watch_query = WatchQuery(query="reinforcement learning", max_results=10)

    # First call -> uncached
    res1 = wrapped.search("case-1", watch_query)
    assert len(res1) == 1
    assert res1[0].title == "Crossref Discovered Paper"
    assert mock_adapter.search.call_count == 1

    # Second call -> cached
    res2 = wrapped.search("case-1", watch_query)
    assert len(res2) == 1
    assert mock_adapter.search.call_count == 1  # Not incremented


def test_cache_invalidation_and_clear(tmp_path: Path):
    """Verify invalidate and clear purge memory and disk caches."""
    cache = AcademicSearchCache(cache_dir=tmp_path / "search_cache")
    records = [_make_dummy_record("Sample Paper")]

    cache.set("query one", "arxiv", records)
    cache.set("query two", "arxiv", records)
    cache.set("query three", "semantic_scholar", records)

    assert cache.get("query one", "arxiv") is not None
    assert cache.get("query two", "arxiv") is not None
    assert cache.get("query three", "semantic_scholar") is not None

    # Invalidate by source
    cache.invalidate(source_type="arxiv")
    assert cache.get("query one", "arxiv") is None
    assert cache.get("query two", "arxiv") is None
    assert cache.get("query three", "semantic_scholar") is not None

    # Clear all
    cache.clear()
    assert cache.get("query three", "semantic_scholar") is None
