"""OpenAlex adapter mapping/retries and multi-source scan behavior."""

import time

import httpx
import pytest
from sqlalchemy import select

from radar.adapters.openalex import OpenAlexSearchAdapter
from radar.models import RetrievalReceipt, ScanRun, Source
from radar.schemas import SourceRecord, WatchQuery
from radar.services.weekly_radar_service import WeeklyRadarService

from test_weekly_radar import INCOMING_QUOTE, ScriptedLLM, _settings


OPENALEX_PAYLOAD = {
    "results": [
        {
            "id": "https://openalex.org/W123456",
            "title": "Retrieval Robustness at Scale",
            "abstract_inverted_index": {
                "We": [0],
                "evaluate": [1],
                "robust": [2],
                "RAG": [3],
                "systems.": [4],
            },
            "authorships": [
                {"author": {"display_name": "Ada Lovelace"}},
                {"author": {}},
            ],
            "doi": "https://doi.org/10.1234/openalex.1",
            "publication_date": "2026-07-01",
            "type": "article",
            "cited_by_count": 7,
            "primary_location": {
                "landing_page_url": "https://journal.example/paper1",
                "source": {"display_name": "Journal of RAG"},
            },
            "best_oa_location": {"pdf_url": "https://journal.example/paper1.pdf"},
        }
    ]
}


def _scripted_client(monkeypatch, handler, calls):
    class FakeClient:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def get(self, url, **kwargs):
            calls.append((url, kwargs))
            return handler(len(calls) - 1, url, kwargs)

    monkeypatch.setattr(httpx, "Client", FakeClient)
    monkeypatch.setattr(time, "sleep", lambda seconds: None)


def _json_response(status: int, payload: dict | None = None, url: str = "") -> httpx.Response:
    return httpx.Response(
        status,
        json=payload if payload is not None else {},
        request=httpx.Request("GET", url or OpenAlexSearchAdapter.endpoint),
    )


def test_openalex_adapter_maps_works_response(monkeypatch):
    calls = []
    _scripted_client(
        monkeypatch,
        lambda index, url, kwargs: _json_response(200, OPENALEX_PAYLOAD, url),
        calls,
    )
    adapter = OpenAlexSearchAdapter(mailto="radar@example.org")
    results = adapter.search("case-1", WatchQuery(query="RAG robustness", max_results=5))

    assert len(results) == 1
    record = results[0]
    assert record.external_id == "openalex:W123456"
    assert record.title == "Retrieval Robustness at Scale"
    assert record.abstract == "We evaluate robust RAG systems."
    assert record.authors == ["Ada Lovelace"]
    assert record.doi == "10.1234/openalex.1"
    assert record.url == "https://journal.example/paper1"
    assert record.pdf_url == "https://journal.example/paper1.pdf"
    assert record.published_at == "2026-07-01"
    assert record.venue == "Journal of RAG"
    assert record.publication_type == "journal_article"
    assert record.cited_by_count == 7

    params = calls[0][1]["params"]
    assert params["search"] == "RAG robustness"
    assert params["filter"].startswith("from_publication_date:")
    assert "type:article|preprint" in params["filter"]
    assert params["sort"] == "publication_date:desc"
    assert params["per-page"] == 5
    assert params["mailto"] == "radar@example.org"


def test_openalex_search_scopes_to_cs_topic_field_by_default(monkeypatch):
    """Keyword search is scoped to Computer Science (fields/17) by default.

    Without the discipline filter, keyword queries pull in papers from
    unrelated fields (medicine, power systems) that merely mention the terms.
    """

    calls = []
    _scripted_client(
        monkeypatch,
        lambda index, url, kwargs: _json_response(200, OPENALEX_PAYLOAD, url),
        calls,
    )
    adapter = OpenAlexSearchAdapter(mailto="radar@example.org")
    adapter.search("case-1", WatchQuery(query="transformer translation", max_results=5))

    assert "primary_topic.field.id:17" in calls[0][1]["params"]["filter"]


def test_openalex_topic_filter_can_be_disabled(monkeypatch):
    calls = []
    _scripted_client(
        monkeypatch,
        lambda index, url, kwargs: _json_response(200, OPENALEX_PAYLOAD, url),
        calls,
    )
    adapter = OpenAlexSearchAdapter(mailto="radar@example.org", topic_field_ids=None)
    adapter.search("case-1", WatchQuery(query="transformer translation", max_results=5))

    assert "primary_topic.field.id" not in calls[0][1]["params"]["filter"]


def test_openalex_lookback_days_moves_date_filter(monkeypatch):
    calls = []
    _scripted_client(
        monkeypatch,
        lambda index, url, kwargs: _json_response(200, {"results": []}, url),
        calls,
    )
    OpenAlexSearchAdapter(lookback_days=7).search(
        "case-1", WatchQuery(query="RAG robustness", max_results=5)
    )
    default_filter = calls[0][1]["params"]["filter"]

    OpenAlexSearchAdapter(lookback_days=90).search(
        "case-1", WatchQuery(query="RAG robustness", max_results=5)
    )
    wider_filter = calls[1][1]["params"]["filter"]
    assert default_filter != wider_filter


def test_openalex_search_retries_on_429_then_succeeds(monkeypatch):
    calls = []
    _scripted_client(
        monkeypatch,
        lambda index, url, kwargs: (
            _json_response(429, url=url)
            if index < 1
            else _json_response(200, OPENALEX_PAYLOAD, url)
        ),
        calls,
    )
    results = OpenAlexSearchAdapter().search(
        "case-1", WatchQuery(query="RAG robustness", max_results=5)
    )
    assert len(results) == 1
    assert len(calls) == 2


def test_openalex_search_gives_up_after_max_retries(monkeypatch):
    calls = []
    _scripted_client(
        monkeypatch, lambda index, url, kwargs: _json_response(500, url=url), calls
    )
    with pytest.raises(RuntimeError, match="openalex_search_failed"):
        OpenAlexSearchAdapter().search(
            "case-1", WatchQuery(query="RAG robustness", max_results=5)
        )
    assert len(calls) == 1 + 3


def _record(external_id, *, doi=None, title="A Matched Re-evaluation of RadarNet"):
    return SourceRecord(
        external_id=external_id,
        title=title,
        authors=["Independent Lab"],
        abstract=INCOMING_QUOTE,
        url=f"https://example.org/{external_id}",
        published_at="2026-07-20T00:00:00Z",
        doi=doi,
        cited_by_count=12,
    )


class FixedSearch:
    def __init__(self, records):
        self.records = records

    def search(self, case_id, watch_query):
        return list(self.records)


class FailingSearch:
    def search(self, case_id, watch_query):
        raise RuntimeError("openalex_search_failed: boom")


def test_multi_source_scan_dedupes_by_doi(db_session_factory, golden_case):
    scan_id = WeeklyRadarService(
        db_session_factory,
        search_adapters=[
            FixedSearch([_record("arxiv:dup-1", doi="10.1234/dup")]),
            FixedSearch([_record("openalex:dup-1", doi="10.1234/dup")]),
        ],
        llm_client=ScriptedLLM(),
        settings=_settings(),
    ).run(golden_case, query="DomainQA RadarNet", max_results=5, analysis_limit=1)

    with db_session_factory() as session:
        scan = session.get(ScanRun, scan_id)
        receipts = list(
            session.scalars(
                select(RetrievalReceipt).where(RetrievalReceipt.scan_run_id == scan_id)
            )
        )
        source = session.scalar(
            select(Source).where(Source.doi == "10.1234/dup")
        )
        assert scan.status == "completed"
        assert scan.stats_json["scanned_papers"] == 1
        assert scan.stats_json["source_counts"] == {"fixedsearch": 2}
        assert scan.stats_json["source_failures"] == {}
        assert len(receipts) == 2
        assert {receipt.status for receipt in receipts} == {"success"}
        assert source is not None
        assert source.cited_by_count == 12


def test_multi_source_scan_dedupes_by_normalized_title(db_session_factory, golden_case):
    scan_id = WeeklyRadarService(
        db_session_factory,
        search_adapters=[
            FixedSearch([_record("arxiv:t-1")]),
            FixedSearch(
                [_record("openalex:t-1", title="A Matched Re-Evaluation of RadarNet!")]
            ),
        ],
        llm_client=ScriptedLLM(),
        settings=_settings(),
    ).run(golden_case, query="DomainQA RadarNet", max_results=5, analysis_limit=1)

    with db_session_factory() as session:
        scan = session.get(ScanRun, scan_id)
        assert scan.stats_json["scanned_papers"] == 1


def test_single_source_failure_degrades_but_records_stats(
    db_session_factory, golden_case
):
    scan_id = WeeklyRadarService(
        db_session_factory,
        search_adapters=[FixedSearch([_record("arxiv:ok-1")]), FailingSearch()],
        llm_client=ScriptedLLM(),
        settings=_settings(),
    ).run(golden_case, query="DomainQA RadarNet", max_results=5, analysis_limit=1)

    with db_session_factory() as session:
        scan = session.get(ScanRun, scan_id)
        assert scan.status == "completed"
        assert scan.stats_json["scanned_papers"] == 1
        assert "openalex_search_failed" in scan.stats_json["source_failures"]["failingsearch"]


def test_all_sources_failing_fails_scan_explicitly(db_session_factory, golden_case):
    service = WeeklyRadarService(
        db_session_factory,
        search_adapters=[FailingSearch(), FailingSearch()],
        llm_client=ScriptedLLM(),
        settings=_settings(),
    )
    with pytest.raises(RuntimeError, match="all_search_sources_failed"):
        service.run(
            golden_case, query="DomainQA RadarNet", max_results=5, analysis_limit=1
        )


def test_quality_bonus_is_bounded_and_citation_aware():
    bonus = WeeklyRadarService._quality_bonus
    assert bonus(None, None) == 0.0
    assert bonus(0, "arXiv") == 0.0
    assert bonus(0, "Nature") == pytest.approx(0.02)
    assert bonus(3, None) == pytest.approx(0.01 * min(1.3862943611198906, 4.0))
    # Even a heavily cited journal paper stays a tie-breaker, never a decider.
    assert bonus(1_000_000, "Nature") <= 0.07
