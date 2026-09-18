"""Chinese-language OpenAlex adapter tests with scripted HTTP responses."""

import httpx

from radar.adapters.chinese_literature import ChineseLiteratureSearchAdapter
from radar.schemas import WatchQuery


PAYLOAD = {
    "results": [
        {
            "id": "https://openalex.org/WCN123",
            "title": "中文检索研究",
            "abstract_inverted_index": {"中文": [0], "检索": [1], "研究": [2]},
            "authorships": [{"author": {"display_name": "张三"}}],
            "doi": "https://doi.org/10.1234/chinese.1",
            "publication_date": "2026-08-01",
            "type": "article",
            "primary_location": {
                "landing_page_url": "https://example.org/chinese-paper",
                "source": {"display_name": "中文人工智能学报"},
            },
        }
    ]
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


def test_chinese_literature_adapter_reuses_openalex_parsing(monkeypatch):
    clients = []

    def build_client(**kwargs):
        client = ScriptedClient(**kwargs)
        clients.append(client)
        return client

    monkeypatch.setattr(httpx, "Client", build_client)
    adapter = ChineseLiteratureSearchAdapter(mailto="radar@example.org")

    records = adapter.search(
        "case-1", WatchQuery(query="中文检索", max_results=100)
    )

    assert len(records) == 1
    record = records[0]
    assert record.source_kind == "chinese_literature"
    assert record.external_id == "openalex:WCN123"
    assert record.title == "中文检索研究"
    assert record.abstract == "中文 检索 研究"
    assert record.authors == ["张三"]
    assert record.doi == "10.1234/chinese.1"

    assert clients[0].calls[0][1]["params"] == {
        "search": "中文检索",
        "filter": "language:zh,type:article|preprint",
        "per-page": 50,
        "mailto": "radar@example.org",
    }
