"""Literature Q&A service: retrieval over stored sources + LLM answer."""

from sqlalchemy import select
from starlette.testclient import TestClient

from radar.api import app
from radar.config import Settings
from radar.models import SourceSnapshot
from radar.services.literature_qa_service import LiteratureQAService, LiteratureQAAnswer


def _settings():
    return Settings(
        _env_file=None,
        llm_provider="deepseek",
        llm_api_key="sk-test-key-000000000000",
        llm_model="deepseek-v4-pro",
        llm_base_url="https://api.deepseek.com",
    )


class ScriptedQALLM:
    last_receipt = {
        "raw_response": "{}",
        "usage": {"prompt_tokens": 8, "completion_tokens": 4},
        "latency_ms": 2,
    }

    def generate_structured(self, *, stage, prompt, response_model, max_tokens=None):
        assert stage == "literature_qa"
        return LiteratureQAAnswer(
            answer="The evidence suggests retrieval-augmented generation improves answer quality.",
            source_ids=[],
            evidence_snippets=["A snippet from the top paper"],
            uncertainty="",
        )


def test_literature_qa_answers_from_stored_sources(db_session_factory, golden_case):
    service = LiteratureQAService(
        db_session_factory,
        llm_client=ScriptedQALLM(),
        settings=_settings(),
    )

    result = service.answer(
        golden_case,
        "How does retrieval-augmented generation help question answering?",
        top_k=3,
    )

    assert result["answer"].startswith("The evidence suggests")
    assert result["sources"], "expected at least one grounded source"
    assert result["sources"][0]["source_id"]
    assert result["sources"][0]["title"]
    assert result["warnings"] == []

    # The run left its audit trail.
    from radar.models import AuditEvent, ModelRun

    with db_session_factory() as session:
        run = session.scalar(select(ModelRun).where(ModelRun.stage == "literature_qa"))
        assert run is not None
        event = session.scalar(
            select(AuditEvent).where(AuditEvent.event_type == "literature_qa_executed")
        )
        assert event is not None


def test_literature_qa_no_sources_returns_helpful_message(db_session_factory, tmp_path):
    """A case with no stored snapshots should return a clear no-sources answer."""
    from radar.services.case_service import CaseService

    # Use a fresh empty database without loading the demo case.
    empty_case_id = "empty-case"
    with db_session_factory() as session:
        from radar.models import ResearchCase
        session.add(ResearchCase(id=empty_case_id, title="Empty", research_question="?"))
        session.commit()

    service = LiteratureQAService(
        db_session_factory,
        llm_client=ScriptedQALLM(),
        settings=_settings(),
    )
    result = service.answer(empty_case_id, "Any papers?", top_k=3)
    assert result["sources"] == []
    assert "还没有足够的已收集论文" in result["answer"]
    assert "no_sources" in result["warnings"]


def test_literature_qa_api_route_returns_answer(monkeypatch):
    from radar.services.literature_qa_service import LiteratureQAService

    def fake_answer(self, case_id, question, top_k=5, history=None, **kwargs):
        return {
            "answer": "fake answer",
            "sources": [],
            "uncertainty": "",
            "warnings": [],
        }

    monkeypatch.setattr(LiteratureQAService, "answer", fake_answer)

    with TestClient(app) as client:
        resp = client.post(
            "/api/cases/whatever/literature-qa",
            json={"question": "test", "top_k": 3},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["answer"] == "fake answer"


class ScriptedStreamLLM:
    last_receipt = {
        "raw_response": "{}",
        "usage": {"prompt_tokens": 8, "completion_tokens": 4},
        "latency_ms": 2,
    }

    def generate_structured(self, *, stage, prompt, response_model, max_tokens=None):
        return LiteratureQAAnswer(
            answer="Streamed test answer",
            source_ids=[],
            evidence_snippets=[],
            uncertainty="",
        )

    def stream_text(self, *, stage, prompt, max_tokens=None):
        yield "Streamed "
        yield "test "
        yield "answer."


def test_literature_qa_answer_stream(db_session_factory, golden_case):
    service = LiteratureQAService(
        db_session_factory,
        llm_client=ScriptedStreamLLM(),
        settings=_settings(),
    )

    events = list(service.answer_stream(golden_case, "What are the findings?", top_k=3))
    event_names = [e["event"] for e in events]
    assert "sources" in event_names
    assert "token" in event_names
    assert "done" in event_names

    tokens = [e["data"]["delta"] for e in events if e["event"] == "token"]
    assert "".join(tokens) == "Streamed test answer."


def test_literature_qa_stream_api_route(monkeypatch):
    from radar.services.literature_qa_service import LiteratureQAService

    def fake_stream(self, case_id, question, top_k=5, history=None, **kwargs):
        yield {"event": "sources", "data": {"sources": [{"source_id": "s1", "title": "Paper 1"}]}}
        yield {"event": "token", "data": {"delta": "Hello "}}
        yield {"event": "token", "data": {"delta": "world!"}}
        yield {"event": "done", "data": {"answer": "Hello world!", "sources": [], "uncertainty": ""}}

    monkeypatch.setattr(LiteratureQAService, "answer_stream", fake_stream)

    with TestClient(app) as client:
        resp = client.post(
            "/api/cases/test-case/literature-qa/stream",
            json={"question": "test query", "top_k": 3},
        )
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers["content-type"]
        body = resp.text
        assert "event: sources" in body
        assert "event: token" in body
        assert "Hello " in body
        assert "event: done" in body


