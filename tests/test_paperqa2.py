"""PaperQA2 adapter tests: the incoming paper must be fed into the agent's
document library, and run failures must be distinguishable from real
negatives (C6 regression)."""

import asyncio
from types import SimpleNamespace

import pytest

pytest.importorskip("paperqa")

import paperqa

from radar.adapters.paperqa2 import PaperQA2Adapter
from radar.config import Settings


def _settings():
    return Settings(
        _env_file=None,
        llm_provider="deepseek",
        llm_api_key="sk-test-key-000000000000",
        llm_model="deepseek-v4-pro",
        llm_base_url="https://api.deepseek.com",
    )


class FakeDocs:
    def __init__(self, *args, **kwargs):
        self.texts = []

    async def aadd_texts(self, texts, doc, settings=None, embedding_model=None):
        self.texts.extend(texts)
        return True


def _stub_agent_query(monkeypatch, response):
    """Patch paperqa.agent_query to record the docs it received."""
    captured = {"docs": None, "query": None}

    async def fake_agent_query(*, query, settings, docs, agent_type, **kwargs):
        captured["docs"] = docs
        captured["query"] = query
        return response

    monkeypatch.setattr(paperqa, "Docs", FakeDocs)
    monkeypatch.setattr(paperqa, "agent_query", fake_agent_query)
    return captured


def test_paperqa2_feeds_manuscript_text_into_docs(monkeypatch):
    """The incoming paper's text must reach PaperQA2, not an empty library."""
    captured = _stub_agent_query(
        monkeypatch, SimpleNamespace(answer="No evidence found", contexts=[])
    )
    adapter = PaperQA2Adapter(settings=_settings())

    results = adapter.search_contradictions(
        "RadarNet improves exact match over BM25.",
        manuscript_text="Full text of the incoming paper. " * 10,
    )

    assert len(results) == 1
    assert captured["docs"] is not None
    assert any("incoming paper" in text.text for text in captured["docs"].texts)
    assert "CONTRADICT" in captured["query"]


def test_paperqa2_feeds_paper_paths_into_docs(monkeypatch, tmp_path):
    captured = _stub_agent_query(
        monkeypatch, SimpleNamespace(answer="This contradicts the claim.", contexts=[])
    )
    paper = tmp_path / "incoming.pdf.txt"
    paper.write_text("The opposite result was measured in all conditions.", encoding="utf-8")
    adapter = PaperQA2Adapter(settings=_settings())

    results = adapter.search_supporting(
        "The result holds in all conditions.", paper_paths=[str(paper)]
    )

    assert len(results) == 1
    texts = captured["docs"].texts
    assert any("opposite result" in text.text for text in texts)
    assert all("SUPPORT" in captured["query"] for _ in results)


def test_paperqa2_assess_claim_passes_text_to_both_directions(monkeypatch):
    captured = _stub_agent_query(
        monkeypatch, SimpleNamespace(answer="Evidence supports the claim.", contexts=[])
    )
    adapter = PaperQA2Adapter(settings=_settings())

    result = adapter.assess_claim(
        "RadarNet improves exact match.",
        manuscript_text="The incoming paper reports a gain. " * 5,
    )

    assert result["verdict"] == "supported"
    # both directions ran over the same fed-in text
    assert captured["docs"] is not None
    assert any("incoming paper" in text.text for text in captured["docs"].texts)


def test_paperqa2_run_failure_returns_no_results_not_fake_answer(monkeypatch):
    """A failed run must surface as 'no contradiction found by this run',
    never as a hallucinated verdict."""

    async def failing_agent_query(*, query, settings, docs, agent_type, **kwargs):
        raise RuntimeError("llm call failed")

    monkeypatch.setattr(paperqa, "Docs", FakeDocs)
    monkeypatch.setattr(paperqa, "agent_query", failing_agent_query)
    adapter = PaperQA2Adapter(settings=_settings())

    assert adapter.search_contradictions("Some claim", manuscript_text="text") == []
    result = adapter.assess_claim("Some claim", manuscript_text="text")
    assert result["verdict"] == "unverified"
    assert result["contradictions"] == []
    assert result["supporting"] == []


def test_paperqa2_explicit_negative_does_not_flip_verdict(monkeypatch):
    """An answer stating 'no evidence found' must not be counted as a
    contradiction (or a support) — keyword presence alone is not a verdict."""
    captured = _stub_agent_query(
        monkeypatch,
        SimpleNamespace(answer="I found no evidence that contradicts this claim.", contexts=[]),
    )
    adapter = PaperQA2Adapter(settings=_settings())

    result = adapter.assess_claim("Some claim", manuscript_text="text")

    assert result["verdict"] == "unverified"
    assert result["contradictions"] == []
    assert result["supporting"] == []
    assert captured["query"] is not None  # the run did happen


def test_paperqa2_empty_answer_is_a_real_negative(monkeypatch):
    """An empty answer from a successful run is a negative, not a failure."""
    captured = _stub_agent_query(
        monkeypatch, SimpleNamespace(answer="", contexts=[])
    )
    adapter = PaperQA2Adapter(settings=_settings())

    results = adapter.search_contradictions("Some claim", manuscript_text="text")

    assert results == []
    # the run did happen — this is 'found nothing', unlike a failed run
    assert captured["query"] is not None
