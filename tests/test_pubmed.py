"""PubMed adapter tests (offline, scripted NCBI responses)."""

import httpx
import pytest

from radar.adapters.pubmed import PubmedSearchAdapter
from radar.schemas import WatchQuery

SEARCH_JSON = """{
  "esearchresult": {
    "idlist": ["12345", "67890"]
  }
}
"""

EFETCH_XML = """<?xml version="1.0" encoding="UTF-8"?>
<PubmedArticleSet>
  <PubmedArticle>
    <MedlineCitation>
      <PMID>12345</PMID>
      <Article>
        <Journal>
          <Title>Nature</Title>
          <JournalIssue><PubDate><Year>2026</Year></PubDate></JournalIssue>
        </Journal>
        <ArticleTitle>A biomedical retrieval study</ArticleTitle>
        <Abstract><AbstractText>We study retrieval in biomedical literature.</AbstractText></Abstract>
        <AuthorList>
          <Author><LastName>Zhang</LastName><Initials>W</Initials></Author>
          <Author><LastName>Li</LastName><Initials>Q</Initials></Author>
        </AuthorList>
        <ArticleIdList>
          <ArticleId IdType="pubmed">12345</ArticleId>
          <ArticleId IdType="doi">10.1000/xyz</ArticleId>
        </ArticleIdList>
      </Article>
      <MeshHeadingList>
        <MeshHeading><DescriptorName>Machine Learning</DescriptorName></MeshHeading>
        <MeshHeading><DescriptorName>Information Retrieval</DescriptorName></MeshHeading>
      </MeshHeadingList>
    </MedlineCitation>
  </PubmedArticle>
</PubmedArticleSet>
"""


class FakeClient:
    def __init__(self, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def get(self, url, params=None):
        if "esearch.fcgi" in url:
            return httpx.Response(200, text=SEARCH_JSON, request=httpx.Request("GET", url))
        if "efetch.fcgi" in url:
            return httpx.Response(200, text=EFETCH_XML, request=httpx.Request("GET", url))
        raise AssertionError(f"unexpected url: {url}")


def test_pubmed_adapter_parses_search_and_abstracts(monkeypatch):
    monkeypatch.setattr(httpx, "Client", FakeClient)
    adapter = PubmedSearchAdapter(email="test@example.com")

    records = adapter.search("test-case", WatchQuery(query="biomedical retrieval", max_results=2))

    assert len(records) == 1
    record = records[0]
    assert record.source_kind == "pubmed"
    assert record.external_id == "pubmed:12345"
    assert record.title == "A biomedical retrieval study"
    assert "biomedical" in record.abstract
    assert record.authors == ["Zhang W", "Li Q"]
    assert record.venue == "Nature"
    assert record.doi == "10.1000/xyz"
    assert record.fields_of_study == ["Machine Learning", "Information Retrieval"]
    assert record.published_at == "2026-01-01"


def test_pubmed_empty_results_returns_empty_list(monkeypatch):
    class EmptyClient(FakeClient):
        def get(self, url, params=None):
            if "esearch.fcgi" in url:
                return httpx.Response(
                    200,
                    text='{"esearchresult": {"idlist": []}}',
                    request=httpx.Request("GET", url),
                )
            raise AssertionError(f"unexpected url: {url}")

    monkeypatch.setattr(httpx, "Client", EmptyClient)
    adapter = PubmedSearchAdapter(email="test@example.com")
    assert adapter.search("c", WatchQuery(query="nothing", max_results=3)) == []
