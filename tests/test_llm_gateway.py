"""Tests for the LLM Gateway: multi-model fallback, timeout control, and circuit breaker."""

from collections.abc import Iterator
import httpx
from pydantic import BaseModel

from radar.config import Settings
from radar.llm.base import LLMClient
from radar.llm.factory import build_analysis_llm, describe_fallback_setup
from radar.llm.gateway import FallbackLLMClient


class SampleResponse(BaseModel):
    summary: str
    score: int


class MockFailingLLM:
    """Mock LLM client that always fails with a specified exception."""

    def __init__(self, exc: Exception):
        self.exc = exc
        self.calls = 0
        self.provider_name = "failing_mock"
        self.model_name = "failing-model"
        self.last_receipt = {"latency_ms": 100}

    def generate_structured(self, *, stage: str, prompt: str, response_model, max_tokens=None):
        self.calls += 1
        raise self.exc

    def stream_text(self, *, stage: str, prompt: str, max_tokens=None) -> Iterator[str]:
        self.calls += 1
        raise self.exc
        yield ""  # make it a generator


class MockSuccessLLM:
    """Mock LLM client that succeeds."""

    def __init__(self, output: SampleResponse, name: str = "success-model"):
        self.output = output
        self.calls = 0
        self.provider_name = "success_mock"
        self.model_name = name
        self.last_receipt = {"latency_ms": 50, "usage": {"total_tokens": 42}}

    def generate_structured(self, *, stage: str, prompt: str, response_model, max_tokens=None):
        self.calls += 1
        return self.output

    def stream_text(self, *, stage: str, prompt: str, max_tokens=None) -> Iterator[str]:
        self.calls += 1
        yield "Hello "
        yield "from "
        yield self.model_name


def test_gateway_primary_success():
    primary = MockSuccessLLM(SampleResponse(summary="primary", score=10), name="primary-mod")
    fallback = MockSuccessLLM(SampleResponse(summary="fallback", score=5), name="fallback-mod")

    gateway = FallbackLLMClient(primary, fallback, primary_timeout_seconds=8.0)
    res = gateway.generate_structured(
        stage="test",
        prompt="test",
        response_model=SampleResponse,
    )
    assert res.summary == "primary"
    assert primary.calls == 1
    assert fallback.calls == 0
    assert gateway.last_receipt.get("fallback_used") is False


def test_gateway_primary_timeout_fallback():
    primary = MockFailingLLM(httpx.TimeoutException("Primary call exceeded 8.0s timeout"))
    fallback = MockSuccessLLM(SampleResponse(summary="fallback", score=9), name="fallback-mod")

    gateway = FallbackLLMClient(primary, fallback, primary_timeout_seconds=8.0)
    res = gateway.generate_structured(
        stage="test",
        prompt="test",
        response_model=SampleResponse,
    )
    assert res.summary == "fallback"
    assert primary.calls == 1
    assert fallback.calls == 1
    assert gateway.last_receipt.get("fallback_used") is True


def test_gateway_primary_400_geo_fallback():
    req = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
    resp = httpx.Response(400, request=req)
    primary = MockFailingLLM(httpx.HTTPStatusError("Country unsupported", request=req, response=resp))
    fallback = MockSuccessLLM(SampleResponse(summary="fallback-from-400", score=8), name="fallback-mod")

    gateway = FallbackLLMClient(primary, fallback)
    res = gateway.generate_structured(
        stage="test",
        prompt="test",
        response_model=SampleResponse,
    )
    assert res.summary == "fallback-from-400"
    assert primary.calls == 1
    assert fallback.calls == 1
    assert gateway.last_receipt.get("fallback_used") is True


def test_gateway_primary_429_rate_limit_fallback():
    req = httpx.Request("POST", "https://api.deepseek.com/chat/completions")
    resp = httpx.Response(429, request=req)
    primary = MockFailingLLM(httpx.HTTPStatusError("Rate limited", request=req, response=resp))
    fallback = MockSuccessLLM(SampleResponse(summary="fallback-from-429", score=8), name="fallback-mod")

    gateway = FallbackLLMClient(primary, fallback)
    res = gateway.generate_structured(
        stage="test",
        prompt="test",
        response_model=SampleResponse,
    )
    assert res.summary == "fallback-from-429"
    assert primary.calls == 1
    assert fallback.calls == 1


def test_gateway_primary_504_gateway_timeout_fallback():
    req = httpx.Request("POST", "https://api.relay.com/v1/chat/completions")
    resp = httpx.Response(504, request=req)
    primary = MockFailingLLM(httpx.HTTPStatusError("Gateway Timeout", request=req, response=resp))
    fallback = MockSuccessLLM(SampleResponse(summary="fallback-from-504", score=7), name="fallback-mod")

    gateway = FallbackLLMClient(primary, fallback)
    res = gateway.generate_structured(
        stage="test",
        prompt="test",
        response_model=SampleResponse,
    )
    assert res.summary == "fallback-from-504"
    assert primary.calls == 1
    assert fallback.calls == 1


def test_gateway_stream_text_fallback():
    primary = MockFailingLLM(httpx.ConnectError("Connection refused"))
    fallback = MockSuccessLLM(SampleResponse(summary="fallback", score=10), name="fallback-mod")

    gateway = FallbackLLMClient(primary, fallback)
    tokens = list(gateway.stream_text(stage="test", prompt="test"))
    assert "".join(tokens) == "Hello from fallback-mod"
    assert primary.calls == 1
    assert fallback.calls == 1


def test_gateway_circuit_breaker_and_cooldown():
    primary = MockFailingLLM(httpx.TimeoutException("Timeout"))
    fallback = MockSuccessLLM(SampleResponse(summary="fallback", score=10), name="fallback-mod")

    gateway = FallbackLLMClient(
        primary,
        fallback,
        circuit_breaker_threshold=3,
        circuit_breaker_cooldown_seconds=60.0,
    )

    # 1st failure
    gateway.generate_structured(stage="s", prompt="p", response_model=SampleResponse)
    assert primary.calls == 1
    assert not gateway.is_primary_circuit_open()

    # 2nd failure
    gateway.generate_structured(stage="s", prompt="p", response_model=SampleResponse)
    assert primary.calls == 2
    assert not gateway.is_primary_circuit_open()

    # 3rd failure -> trips breaker!
    gateway.generate_structured(stage="s", prompt="p", response_model=SampleResponse)
    assert primary.calls == 3
    assert gateway.is_primary_circuit_open()

    # 4th call while circuit is open -> should directly call fallback without incrementing primary.calls
    gateway.generate_structured(stage="s", prompt="p", response_model=SampleResponse)
    assert primary.calls == 3  # not incremented!
    assert fallback.calls == 4


def test_factory_builds_fallback_gateway():
    settings = Settings(
        _env_file=None,
        llm_provider="deepseek",
        llm_api_key="sk-primary-test-key-000000",
        llm_model="deepseek-v4-flash",
        llm_base_url="https://api.deepseek.com",
        llm_fallback_provider="openai",
        llm_fallback_api_key="sk-fallback-test-key-000000",
        llm_fallback_model="gpt-5.6-terra",
        llm_fallback_base_url="https://api.openai.com/v1",
        llm_primary_timeout_seconds=8.0,
    )

    client = build_analysis_llm(settings)
    assert isinstance(client, FallbackLLMClient)
    assert client.primary_timeout_seconds == 8.0

    desc = describe_fallback_setup(settings)
    assert desc["configured"] is True
    assert desc["model"] == "gpt-5.6-terra"
    assert desc["provider"] == "openai"
    assert desc["primary_timeout_seconds"] == 8.0
