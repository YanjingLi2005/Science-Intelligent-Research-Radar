import httpx
import pytest

from radar.config import Settings
from radar.llm.ollama import OllamaLLMClient
from radar.schemas import EvidenceSpan


def test_ollama_client_uses_native_json_schema(monkeypatch):
    captured = {}

    def fake_post(url, **kwargs):
        captured["url"] = url
        captured["payload"] = kwargs["json"]
        return httpx.Response(
            200,
            request=httpx.Request("POST", url),
            json={
                "message": {"content": '{"quote":"exact","locator":"p:1"}'},
                "prompt_eval_count": 11,
                "eval_count": 7,
            },
        )

    monkeypatch.setattr("radar.llm.ollama.httpx.post", fake_post)
    settings = Settings(
        _env_file=None,
        local_llm_model="qwen3:4b",
        local_llm_base_url="http://127.0.0.1:11434",
    )
    client = OllamaLLMClient(settings)
    result = client.generate_structured(
        stage="evidence",
        prompt="Return exact evidence.",
        response_model=EvidenceSpan,
    )

    assert result.quote == "exact"
    assert captured["url"].endswith("/api/chat")
    assert captured["payload"]["model"] == "qwen3:4b"
    assert captured["payload"]["format"]["properties"]["quote"]
    assert captured["payload"]["think"] is False
    assert captured["payload"]["options"]["temperature"] == 0
    assert client.last_receipt["usage"] == {
        "prompt_tokens": 11,
        "completion_tokens": 7,
    }


def test_ollama_client_retries_transient_503(monkeypatch):
    calls = []

    def fake_post(url, **kwargs):
        calls.append(url)
        request = httpx.Request("POST", url)
        if len(calls) == 1:
            return httpx.Response(503, request=request)
        return httpx.Response(
            200,
            request=request,
            json={
                "message": {"content": '{"quote":"exact","locator":"p:1"}'},
                "prompt_eval_count": 3,
                "eval_count": 2,
            },
        )

    monkeypatch.setattr("radar.llm.ollama.httpx.post", fake_post)
    settings = Settings(
        _env_file=None,
        local_llm_model="qwen3:4b",
        local_llm_base_url="http://127.0.0.1:11434",
        llm_max_retries=2,
        llm_retry_backoff_seconds=0,
    )
    client = OllamaLLMClient(settings)
    result = client.generate_structured(
        stage="evidence",
        prompt="Return exact evidence.",
        response_model=EvidenceSpan,
    )

    assert result.quote == "exact"
    assert len(calls) == 2
    assert client.last_receipt["attempts"] == 2


def test_ollama_client_does_not_retry_schema_validation_errors(monkeypatch):
    calls = []

    def fake_post(url, **kwargs):
        calls.append(url)
        request = httpx.Request("POST", url)
        return httpx.Response(
            200,
            request=request,
            json={"message": {"content": '{"unexpected": true}'}},
        )

    monkeypatch.setattr("radar.llm.ollama.httpx.post", fake_post)
    settings = Settings(
        _env_file=None,
        local_llm_model="qwen3:4b",
        local_llm_base_url="http://127.0.0.1:11434",
        llm_max_retries=2,
        llm_retry_backoff_seconds=0,
    )
    client = OllamaLLMClient(settings)
    with pytest.raises(RuntimeError, match="ollama_structured_output_failed:evidence"):
        client.generate_structured(
            stage="evidence",
            prompt="Return exact evidence.",
            response_model=EvidenceSpan,
        )
    assert len(calls) == 1
