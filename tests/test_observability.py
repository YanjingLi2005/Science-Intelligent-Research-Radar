"""Unit tests for tracing context, structured error envelopes, and self-correction."""

import httpx
from pydantic import BaseModel, Field
from starlette.testclient import TestClient

from radar.api import app
from radar.errors import AppException, ErrorCode, format_error_envelope
from radar.llm.ollama import OllamaLLMClient
from radar.llm.provider import ProviderLLMClient, _with_parse_feedback
from radar.tracing import Timer, get_trace_id, set_trace_id


def test_tracing_context_management():
    tid1 = set_trace_id()
    assert tid1.startswith("tr-")
    assert get_trace_id() == tid1

    tid2 = set_trace_id("tr-custom-test-id")
    assert tid2 == "tr-custom-test-id"
    assert get_trace_id() == "tr-custom-test-id"


def test_timer():
    t = Timer()
    ms = t.stop()
    assert isinstance(ms, int)
    assert ms >= 0


def test_format_error_envelope():
    env = format_error_envelope("Not found", status_code=404, trace_id="tr-test-404")
    assert env["detail"] == "Not found"
    assert env["error_code"] == ErrorCode.NOT_FOUND.value
    assert env["retryable"] is False
    assert env["trace_id"] == "tr-test-404"

    env_504 = format_error_envelope("Timeout", status_code=504, trace_id="tr-test-504")
    assert env_504["retryable"] is True
    assert env_504["error_code"] == ErrorCode.LLM_TIMEOUT.value


def test_tracing_middleware_generates_and_echoes_trace_id():
    with TestClient(app) as client:
        resp = client.get("/api/health")
        assert resp.status_code == 200
        assert "x-trace-id" in resp.headers
        assert resp.headers["x-trace-id"].startswith("tr-")
        assert "x-response-time-ms" in resp.headers


def test_tracing_middleware_preserves_incoming_trace_id():
    with TestClient(app) as client:
        custom_id = "tr-incoming-client-123"
        resp = client.get("/api/health", headers={"X-Trace-Id": custom_id})
        assert resp.status_code == 200
        assert resp.headers["x-trace-id"] == custom_id


def test_http_exception_returns_unified_error_envelope():
    with TestClient(app) as client:
        resp = client.get("/api/cases/non-existent-case-id-12345/radar")
        assert resp.status_code in {401, 404}
        body = resp.json()
        assert "detail" in body
        assert "error_code" in body
        assert "trace_id" in body
        assert "retryable" in body


def test_with_parse_feedback_payload_enhancement():
    payload = {
        "model": "deepseek-v4-pro",
        "messages": [{"role": "user", "content": "Extract claims"}],
    }
    bad_json = '{"claims": [123]}'
    exc = ValueError("claims items must be objects")

    enhanced = _with_parse_feedback(payload, bad_json, exc)
    msgs = enhanced["messages"]
    assert len(msgs) == 3
    assert msgs[1]["role"] == "assistant"
    assert msgs[1]["content"] == bad_json
    assert msgs[2]["role"] == "user"
    assert "Your previous output failed validation" in msgs[2]["content"]


class SimpleTestModel(BaseModel):
    name: str
    score: int


def test_provider_llm_self_correction_flow(monkeypatch):
    from radar.config import Settings

    calls = []

    def fake_post(url, **kwargs):
        calls.append(kwargs["json"])
        if len(calls) == 1:
            # First attempt returns invalid JSON schema (wrong type for score)
            return httpx.Response(
                200,
                request=httpx.Request("POST", url),
                json={
                    "choices": [{"message": {"content": '{"name": "test", "score": "invalid_number"}'}}],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 5},
                },
            )
        # Second attempt returns corrected valid JSON
        return httpx.Response(
            200,
            request=httpx.Request("POST", url),
            json={
                "choices": [{"message": {"content": '{"name": "test", "score": 95}'}}],
                "usage": {"prompt_tokens": 20, "completion_tokens": 8},
            },
        )

    monkeypatch.setattr("radar.llm.provider.httpx.post", fake_post)

    settings = Settings(
        _env_file=None,
        llm_provider="deepseek",
        llm_api_key="sk-test-key-000000000000",
        llm_model="deepseek-v4-pro",
        llm_base_url="https://api.deepseek.com",
        llm_max_retries=2,
        llm_retry_backoff_seconds=0,
    )
    client = ProviderLLMClient(settings)
    result = client.generate_structured(
        stage="test_stage",
        prompt="Score this item",
        response_model=SimpleTestModel,
    )

    assert result.name == "test"
    assert result.score == 95
    assert len(calls) == 2
    assert client.last_receipt["self_correction_attempts"] == 1
    assert client.last_receipt["trace_id"].startswith("tr-")

