import time

import httpx
import pytest

from radar.adapters.arxiv import ArxivSearchAdapter
from radar.adapters.fallback import FallbackSearchAdapter
from radar.adapters.fixture import FixtureSearchAdapter
from radar.schemas import WatchQuery


class FailingLiveAdapter:
    def search(self, case_id, watch_query):
        raise RuntimeError("429 rate limited")


def test_live_failure_falls_back_to_offline_fixture(golden_dir):
    adapter = FallbackSearchAdapter(
        FailingLiveAdapter(), FixtureSearchAdapter(golden_dir)
    )
    results = adapter.search(
        "case-demo-radar", WatchQuery(query="DomainQA", max_results=10)
    )
    assert results
    assert adapter.used_fallback is True
    assert "429" in adapter.last_error


def test_arxiv_adapter_parses_atom_response(tmp_path, monkeypatch):
    atom = """<?xml version="1.0" encoding="UTF-8"?>
    <feed xmlns="http://www.w3.org/2005/Atom"
          xmlns:arxiv="http://arxiv.org/schemas/atom">
      <entry>
        <id>https://arxiv.org/abs/2506.00789v1</id>
        <updated>2025-06-01T00:00:00Z</updated>
        <published>2025-06-01T00:00:00Z</published>
        <title>Retrieval Robustness Evaluation</title>
        <summary>We evaluate RAG robustness.</summary>
        <author><name>Test Author</name></author>
        <link href="https://arxiv.org/abs/2506.00789v1" rel="alternate" />
        <link title="pdf" href="https://arxiv.org/pdf/2506.00789v1" rel="related" type="application/pdf" />
        <arxiv:journal_ref>Journal of Reliable RAG 12 (2025)</arxiv:journal_ref>
        <arxiv:doi>10.1234/reliable-rag.2025.7</arxiv:doi>
        <arxiv:license>http://arxiv.org/licenses/nonexclusive-distrib/1.0/</arxiv:license>
        <arxiv:primary_category term="cs.CL" />
      </entry>
    </feed>"""

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def get(self, url, **kwargs):
            assert kwargs["params"]["search_query"] == "all:rag AND all:robustness"
            return httpx.Response(
                200, text=atom, request=httpx.Request("GET", url)
            )

    monkeypatch.setattr(httpx, "Client", FakeClient)
    monkeypatch.setattr("radar.adapters.arxiv._last_request_at", 0.0)
    results = ArxivSearchAdapter(tmp_path).search(
        "test-case", WatchQuery(query="RAG robustness", max_results=3)
    )

    assert len(results) == 1
    assert results[0].external_id == "arxiv:2506.00789v1"
    assert results[0].authors == ["Test Author"]
    assert results[0].venue == "Journal of Reliable RAG 12 (2025)"
    assert results[0].publication_type == "journal_article"
    assert results[0].doi == "10.1234/reliable-rag.2025.7"
    assert results[0].pdf_url == "https://arxiv.org/pdf/2506.00789v1"
    assert results[0].arxiv_primary_category == "cs.CL"
    assert results[0].ccf_rank is None  # unknown journal venue


EMPTY_ATOM = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"></feed>"""

ONE_ENTRY_ATOM = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:arxiv="http://arxiv.org/schemas/atom">
  <entry>
    <id>https://arxiv.org/abs/2506.00789v1</id>
    <published>2025-06-01T00:00:00Z</published>
    <title>Retrieval Robustness Evaluation</title>
    <summary>We evaluate RAG robustness.</summary>
    <author><name>Test Author</name></author>
    <link href="https://arxiv.org/abs/2506.00789v1" rel="alternate" />
  </entry>
</feed>"""


def _scripted_client(monkeypatch, responses):
    """Patch httpx.Client with a fake returning queued responses; return calls."""

    calls = []

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def get(self, url, **kwargs):
            calls.append(url)
            return responses(len(calls) - 1)

    monkeypatch.setattr(httpx, "Client", FakeClient)
    monkeypatch.setattr("radar.adapters.arxiv._last_request_at", 0.0)
    monkeypatch.setattr(time, "sleep", lambda seconds: None)
    return calls


def _response(status: int, text: str = "") -> httpx.Response:
    return httpx.Response(
        status, text=text, request=httpx.Request("GET", "https://export.arxiv.org")
    )


def test_arxiv_empty_results_are_not_cached(tmp_path, monkeypatch):
    calls = _scripted_client(
        monkeypatch, lambda index: _response(200, EMPTY_ATOM)
    )
    adapter = ArxivSearchAdapter(tmp_path)
    query = WatchQuery(query="RAG robustness", max_results=3)

    assert adapter.search("test-case", query) == []
    assert not list(tmp_path.glob("arxiv-*.json"))

    # A repeat query hits the API again instead of serving a cached empty day.
    assert adapter.search("test-case", query) == []
    assert len(calls) == 2


def test_arxiv_search_retries_on_429_then_succeeds(tmp_path, monkeypatch):
    responses = lambda index: (
        _response(429) if index < 2 else _response(200, ONE_ENTRY_ATOM)
    )
    calls = _scripted_client(monkeypatch, responses)

    results = ArxivSearchAdapter(tmp_path).search(
        "test-case", WatchQuery(query="RAG robustness", max_results=3)
    )

    assert len(results) == 1
    assert len(calls) == 3


def test_arxiv_search_gives_up_after_max_retries(tmp_path, monkeypatch):
    calls = _scripted_client(monkeypatch, lambda index: _response(500))

    with pytest.raises(RuntimeError, match="arxiv_search_failed"):
        ArxivSearchAdapter(tmp_path).search(
            "test-case", WatchQuery(query="RAG robustness", max_results=3)
        )

    assert len(calls) == 1 + 3  # initial attempt plus MAX_RETRIES retries


def test_arxiv_search_does_not_retry_client_errors(tmp_path, monkeypatch):
    calls = _scripted_client(monkeypatch, lambda index: _response(400))

    with pytest.raises(RuntimeError, match="arxiv_search_failed"):
        ArxivSearchAdapter(tmp_path).search(
            "test-case", WatchQuery(query="RAG robustness", max_results=3)
        )

    assert len(calls) == 1
