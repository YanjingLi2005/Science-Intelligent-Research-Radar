"""Semantic Scholar adapter mapping and bounded retry behavior."""

import time

import httpx

from radar.adapters.semantic_scholar import SemanticScholarAdapter
from radar.schemas import WatchQuery


PAYLOAD = {
    "data": [
        {
            "paperId": "s2-123",
            "title": "Robust Retrieval",
            "abstract": "A study of robust retrieval.",
            "authors": [{"name": "Ada Lovelace"}],
            "year": 2025,
            "publicationDate": "2025-04-02",
            "externalIds": {"DOI": "https://doi.org/10.1234/robust", "ArXiv": "2504.00001"},
            "url": "https://www.semanticscholar.org/paper/s2-123",
            "openAccessPdf": {"url": "https://example.org/robust.pdf"},
            "venue": "Journal of Retrieval",
            "publicationTypes": ["JournalArticle"],
            "citationCount": 19,
        }
    ]
}


def _response(status: int, payload: dict, url: str) -> httpx.Response:
    return httpx.Response(
        status,
        json=payload,
        request=httpx.Request("GET", url),
    )


def test_semantic_scholar_maps_paper_response(monkeypatch):
    calls = []

    class FakeClient:
        def __init__(self, **kwargs):
            self.headers = kwargs["headers"]

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def get(self, url, **kwargs):
            calls.append((url, kwargs, self.headers))
            return _response(200, PAYLOAD, url)

    monkeypatch.setattr(httpx, "Client", FakeClient)
    adapter = SemanticScholarAdapter(api_key="test-key")
    records = adapter.search("case-1", WatchQuery(query="robust retrieval", max_results=5))

    assert len(records) == 1
    record = records[0]
    assert record.source_kind == "semantic_scholar"
    assert record.external_id == "semantic_scholar:s2-123"
    assert record.doi == "10.1234/robust"
    assert record.arxiv_id == "2504.00001"
    assert record.publication_type == "journal_article"
    assert record.cited_by_count == 19
    assert calls[0][1]["params"]["limit"] == 5
    assert calls[0][2]["x-api-key"] == "test-key"


def test_semantic_scholar_retries_rate_limit(monkeypatch):
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
            return _response(429 if len(calls) == 1 else 200, PAYLOAD, url)

    monkeypatch.setattr(httpx, "Client", FakeClient)
    monkeypatch.setattr(time, "sleep", lambda seconds: None)

    records = SemanticScholarAdapter().search(
        "case-1", WatchQuery(query="robust retrieval", max_results=2)
    )
    assert len(records) == 1
    assert len(calls) == 2


def test_semantic_scholar_maps_references_and_citations(monkeypatch):
    responses = [
        {
            "data": [
                {
                    "citedPaper": {
                        **PAYLOAD["data"][0],
                        "paperId": "s2-reference",
                        "title": "Referenced Retrieval",
                    }
                }
            ]
        },
        {
            "data": [
                {
                    "citingPaper": {
                        **PAYLOAD["data"][0],
                        "paperId": "s2-citation",
                        "title": "Citing Retrieval",
                    }
                }
            ]
        },
    ]
    calls = []

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def get(self, url, **kwargs):
            calls.append((url, kwargs))
            return _response(200, responses.pop(0), url)

    monkeypatch.setattr(httpx, "Client", FakeClient)
    adapter = SemanticScholarAdapter()

    references = adapter.get_references("s2-root", max_results=7)
    citations = adapter.get_citations("s2-root", max_results=8)

    assert [record.title for record in references] == ["Referenced Retrieval"]
    assert [record.title for record in citations] == ["Citing Retrieval"]
    assert references[0].source_kind == "semantic_scholar"
    assert citations[0].source_kind == "semantic_scholar"
    assert calls[0][0].endswith("/paper/s2-root/references")
    assert calls[1][0].endswith("/paper/s2-root/citations")
    assert calls[0][1]["params"]["limit"] == 7
    assert calls[1][1]["params"]["limit"] == 8
    assert calls[0][1]["params"]["fields"] == (
        "title,abstract,authors,year,publicationDate,externalIds,url,"
        "openAccessPdf,venue,publicationTypes,citationCount,fieldsOfStudy"
    )
