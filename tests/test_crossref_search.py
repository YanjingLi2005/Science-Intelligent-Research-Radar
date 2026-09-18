"""Crossref search adapter tests with scripted HTTP responses."""

import httpx

from radar.adapters.crossref_search import CrossrefSearchAdapter
from radar.schemas import WatchQuery


PAYLOAD = {
    "message": {
        "items": [
            {
                "DOI": "10.1234/crossref.1",
                "title": ["A Crossref Study"],
                "author": [
                    {"given": "Ada", "family": "Lovelace"},
                    {"given": "Grace", "family": "Hopper"},
                ],
                "container-title": ["Journal of Machine Learning Research"],
                "issued": {"date-parts": [[2026, 8, 7]]},
                "URL": "https://doi.org/10.1234/crossref.1",
                "abstract": (
                    "<jats:p>This is <jats:italic>useful</jats:italic> research.</jats:p>"
                ),
                "type": "journal-article",
            }
        ]
    }
}


class ScriptedClient:
    def __init__(self, **kwargs):
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return httpx.Response(
            200,
            json=PAYLOAD,
            request=httpx.Request("GET", url),
        )


def test_crossref_adapter_parses_works_and_caps_rows(monkeypatch):
    clients = []

    def build_client(**kwargs):
        client = ScriptedClient(**kwargs)
        clients.append(client)
        return client

    monkeypatch.setattr(httpx, "Client", build_client)
    adapter = CrossrefSearchAdapter(mailto="radar@example.org")

    records = adapter.search(
        "case-1", WatchQuery(query="retrieval robustness", max_results=100)
    )

    assert len(records) == 1
    record = records[0]
    assert record.source_kind == "crossref"
    assert record.external_id == "crossref:10.1234/crossref.1"
    assert record.title == "A Crossref Study"
    assert record.authors == ["Ada Lovelace", "Grace Hopper"]
    assert record.abstract == "This is useful research."
    assert record.url == "https://doi.org/10.1234/crossref.1"
    assert record.published_at == "2026-08-07"
    assert record.doi == "10.1234/crossref.1"
    assert record.venue == "Journal of Machine Learning Research"
    assert record.publication_type == "journal_article"
    assert record.fields_of_study == []
    assert record.ccf_rank == "A"

    assert clients[0].calls[0][1]["params"] == {
        "query": "retrieval robustness",
        "rows": 50,
        "select": "DOI,title,author,container-title,issued,URL,abstract",
    }
