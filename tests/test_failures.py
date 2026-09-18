import pytest
import httpx

from radar.config import Settings
from radar.llm.provider import ProviderLLMClient
from radar.schemas import (
    ActionAdviceOutput,
    AtomicClaimBatch,
    ClaimCandidateBatch,
    ClaimSemanticVerification,
    EvidenceFidelityVerdict,
    EvidenceSpan,
    ExtractedReferenceBatch,
    HydeAbstractOutput,
    ImpactAssessmentOutput,
    IncomingResult,
    ManuscriptOverview,
    ManuscriptUnderstandingOutput,
    MultiLocationPatchOutput,
    PatchProposalOutput,
    RerankBatchOutput,
    SearchQueryBatch,
)
from radar.services.claim_pipeline import AttributionOutput, ClassificationOutput

# Every response model used with generate_structured (grep response_model=).
LLM_RESPONSE_MODELS = [
    ActionAdviceOutput,
    AtomicClaimBatch,
    AttributionOutput,
    ClaimCandidateBatch,
    ClaimSemanticVerification,
    ClassificationOutput,
    EvidenceFidelityVerdict,
    ExtractedReferenceBatch,
    HydeAbstractOutput,
    ImpactAssessmentOutput,
    IncomingResult,
    ManuscriptOverview,
    ManuscriptUnderstandingOutput,
    MultiLocationPatchOutput,
    PatchProposalOutput,
    RerankBatchOutput,
    SearchQueryBatch,
]


def test_unconfigured_llm_fails_explicitly():
    settings = Settings(
        _env_file=None, llm_api_key=None, llm_model=None, llm_base_url=None
    )
    with pytest.raises(RuntimeError, match="llm_not_configured"):
        ProviderLLMClient(settings).generate_structured(
            stage="failure_injection", prompt="no call", response_model=EvidenceSpan
        )


def test_deepseek_uses_json_object_and_embeds_schema():
    settings = Settings(
        _env_file=None,
        llm_provider="deepseek",
        llm_api_key="sk-test-key-000000000000",
        llm_model="deepseek-v4-pro",
        llm_base_url="https://api.deepseek.com",
    )

    payload = ProviderLLMClient(settings).build_payload(
        stage="impact_assessment",
        prompt="Evaluate only the supplied evidence.",
        response_model=EvidenceSpan,
    )

    assert payload["model"] == "deepseek-v4-pro"
    assert payload["response_format"] == {"type": "json_object"}
    assert payload["thinking"] == {"type": "enabled"}
    assert payload["reasoning_effort"] == "high"
    assert payload["max_tokens"] == 4096
    assert payload["stream"] is False
    assert payload["messages"][0]["role"] == "system"
    assert "JSON Schema" in payload["messages"][0]["content"]
    assert "quote" in payload["messages"][0]["content"]
    assert payload["messages"][1]["content"] == "Evaluate only the supplied evidence."


def test_extraction_stages_disable_thinking_for_deepseek():
    """Regression: claim extraction on large manuscripts truncates to empty
    content when DeepSeek's hidden reasoning burns the whole output budget
    (finish_reason=length). Extraction stages must run without thinking so the
    token budget goes to the JSON itself, while analysis stages keep the
    user-configured reasoning settings."""
    settings = Settings(
        _env_file=None,
        llm_provider="deepseek",
        llm_api_key="sk-test-key-000000000000",
        llm_model="deepseek-v4-pro",
        llm_base_url="https://api.deepseek.com",
        llm_thinking="enabled",
        llm_reasoning_effort="high",
    )
    client = ProviderLLMClient(settings)

    extraction = client.build_payload(
        stage="claim_extraction",
        prompt="Extract claims.",
        response_model=ClaimCandidateBatch,
    )
    assert extraction["thinking"] == {"type": "disabled"}
    assert extraction["reasoning_effort"] == "low"

    overview = client.build_payload(
        stage="manuscript_overview",
        prompt="Summarize.",
        response_model=ManuscriptOverview,
    )
    assert overview["thinking"] == {"type": "disabled"}

    analysis = client.build_payload(
        stage="impact_assessment",
        prompt="Assess.",
        response_model=EvidenceSpan,
    )
    assert analysis["thinking"] == {"type": "enabled"}
    assert analysis["reasoning_effort"] == "high"


def test_extraction_stages_lower_reasoning_for_openai():
    settings = Settings(
        _env_file=None,
        llm_provider="openai",
        llm_api_key="sk-test-key-000000000000",
        llm_model="gpt-5.6-terra",
        llm_base_url="https://api.openai.com/v1",
        llm_reasoning_effort="high",
    )
    client = ProviderLLMClient(settings)
    assert (
        client.build_payload(
            stage="claim_extraction", prompt="x", response_model=ClaimCandidateBatch
        )["reasoning_effort"]
        == "low"
    )
    assert (
        client.build_payload(
            stage="impact_assessment", prompt="x", response_model=EvidenceSpan
        )["reasoning_effort"]
        == "high"
    )


def test_truncated_non_empty_json_escalates_budget(monkeypatch):
    """Regression: finish_reason='length' with a PARTIAL JSON body (non-empty
    content, e.g. 'Unterminated string') must escalate the token budget and
    retry, just like empty content, instead of failing the parse."""
    payloads = []

    def fake_post(url, json=None, headers=None, timeout=None):
        payloads.append(dict(json or {}))
        request = httpx.Request("POST", url)
        content = (
            '{"quote": "abc"'  # truncated mid-string
            if len(payloads) == 1
            else '{"quote": "abc", "locator": "x"}'
        )
        return httpx.Response(
            200,
            request=request,
            json={
                "choices": [
                    {
                        "message": {"content": content},
                        "finish_reason": "length" if len(payloads) == 1 else "stop",
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            },
        )

    monkeypatch.setattr(httpx, "post", fake_post)
    settings = Settings(
        _env_file=None,
        llm_provider="deepseek",
        llm_api_key="sk-test-key-000000000000",
        llm_model="deepseek-v4-pro",
        llm_base_url="https://api.deepseek.com",
        llm_max_tokens=4096,
        llm_max_retries=1,
        llm_retry_backoff_seconds=0,
    )
    client = ProviderLLMClient(settings)
    result = client.generate_structured(
        stage="evidence", prompt="Return evidence.", response_model=EvidenceSpan
    )
    assert result.quote == "abc"
    assert len(payloads) == 2
    assert payloads[0]["max_tokens"] == 4096
    assert payloads[1]["max_tokens"] == 8192


def test_repeated_truncation_keeps_escalating_budget(monkeypatch):
    """Regression: a very long paper may still truncate at the doubled cap.
    The budget must keep doubling (bounded by the 32768 ceiling) until the
    JSON completes instead of failing on the second truncation."""
    payloads = []

    def fake_post(url, json=None, headers=None, timeout=None):
        payloads.append(dict(json or {}))
        request = httpx.Request("POST", url)
        truncated = '{"quote": "abc"'  # incomplete JSON
        complete = '{"quote": "abc", "locator": "x"}'
        return httpx.Response(
            200,
            request=request,
            json={
                "choices": [
                    {
                        "message": {"content": truncated if len(payloads) < 3 else complete},
                        "finish_reason": "length" if len(payloads) < 3 else "stop",
                    }
                ],
                "usage": {},
            },
        )

    monkeypatch.setattr(httpx, "post", fake_post)
    settings = Settings(
        _env_file=None,
        llm_provider="deepseek",
        llm_api_key="sk-test-key-000000000000",
        llm_model="deepseek-v4-pro",
        llm_base_url="https://api.deepseek.com",
        llm_max_tokens=4096,
        llm_max_retries=3,
        llm_retry_backoff_seconds=0,
    )
    client = ProviderLLMClient(settings)
    result = client.generate_structured(
        stage="evidence", prompt="Return evidence.", response_model=EvidenceSpan
    )
    assert result.locator == "x"
    assert [p["max_tokens"] for p in payloads] == [4096, 8192, 16384]


def test_extraction_stages_get_generous_token_floor():
    settings = Settings(
        _env_file=None,
        llm_provider="deepseek",
        llm_api_key="sk-test-key-000000000000",
        llm_model="deepseek-v4-pro",
        llm_base_url="https://api.deepseek.com",
        llm_max_tokens=4096,
    )
    client = ProviderLLMClient(settings)
    extraction = client.build_payload(
        stage="claim_extraction", prompt="x", response_model=ClaimCandidateBatch
    )
    assert extraction["max_tokens"] == 16384
    overview = client.build_payload(
        stage="manuscript_overview", prompt="x", response_model=ManuscriptOverview
    )
    assert overview["max_tokens"] == 16384
    analysis = client.build_payload(
        stage="impact_assessment", prompt="x", response_model=EvidenceSpan
    )
    assert analysis["max_tokens"] == 4096
    # An explicit max_tokens from the caller wins over the floor.
    explicit = client.build_payload(
        stage="claim_extraction", prompt="x",
        response_model=ClaimCandidateBatch, max_tokens=6000,
    )
    assert explicit["max_tokens"] == 6000


def test_generic_provider_keeps_strict_json_schema():
    settings = Settings(
        _env_file=None,
        llm_provider="openai_compatible",
        llm_api_key="sk-test-key-000000000000",
        llm_model="test-model",
        llm_base_url="https://example.test/v1",
    )

    payload = ProviderLLMClient(settings).build_payload(
        stage="evidence", prompt="Return evidence.", response_model=EvidenceSpan
    )

    assert payload["response_format"]["type"] == "json_schema"
    assert payload["response_format"]["json_schema"]["strict"] is True


def _assert_openai_strict_compliant(node, path="schema"):
    """Walk a JSON Schema and enforce OpenAI strict-mode structural rules."""
    assert isinstance(node, dict), f"{path}: expected object"
    assert "default" not in node, f"{path}: default is rejected in strict mode"
    assert node.get("type") != "null", f"{path}: null type is rejected"
    for key in ("anyOf", "oneOf", "allOf"):
        assert key not in node, f"{path}: {key} must be inlined after hardening"
    if node.get("type") == "object":
        properties = node.get("properties") or {}
        assert node.get("additionalProperties") is False, (
            f"{path}: additionalProperties must be false"
        )
        assert set(node.get("required", [])) == set(properties), (
            f"{path}: required must cover every property"
        )
        for name, prop in properties.items():
            _assert_openai_strict_compliant(prop, f"{path}.{name}")
    elif node.get("type") == "array":
        items = node.get("items")
        if isinstance(items, dict):
            _assert_openai_strict_compliant(items, f"{path}[]")


@pytest.mark.parametrize("model", LLM_RESPONSE_MODELS)
def test_openai_strict_schema_is_compliant_for_all_response_models(model):
    """C5 regression: OpenAI rejects pydantic's raw model_json_schema with
    HTTP 400; every response model must harden into a strict-compliant schema."""
    settings = Settings(
        _env_file=None,
        llm_provider="openai",
        llm_api_key="sk-test-key-000000000000",
        llm_model="gpt-5.6-terra",
        llm_base_url="https://api.openai.com/v1",
    )
    payload = ProviderLLMClient(settings).build_payload(
        stage=model.__name__, prompt="Return evidence.", response_model=model
    )
    schema = payload["response_format"]["json_schema"]["schema"]
    _assert_openai_strict_compliant(schema)

    # The hardened schema keeps the model's top-level property names intact.
    raw = model.model_json_schema()
    assert set(schema["properties"]) == set(raw["properties"])
    # Optional fields stay parseable: pydantic still accepts a payload that
    # omits them (missing → default), so hardened schemas round-trip.
    sample = model.model_validate(sample_payload(model))
    assert isinstance(sample, model)


def sample_payload(model) -> dict:
    """Minimal valid payload for the schema's own property names."""

    schema = model.model_json_schema()
    defs = schema.get("$defs") or {}

    def resolve(node):
        seen = 0
        while isinstance(node, dict) and seen < 8:
            if "$ref" in node:
                name = node["$ref"].rsplit("/", 1)[-1]
                node = defs.get(name, node)
                seen += 1
            elif "anyOf" in node:
                branches = [b for b in node["anyOf"] if b.get("type") != "null"]
                node = branches[0] if branches else node["anyOf"][0]
                seen += 1
            else:
                break
        return node

    def build_for(node):
        node = resolve(node)
        ntype = node.get("type")
        if ntype == "array":
            return []
        if ntype == "object":
            return {
                name: build_for(prop)
                for name, prop in (node.get("properties") or {}).items()
            }
        if ntype == "integer":
            return 0
        if ntype == "number":
            return 0.0
        if ntype == "boolean":
            return False
        if node.get("enum"):
            return node["enum"][0]
        return "x"  # non-blank: models with blank-forbidding validators reject ""

    return {
        name: build_for(prop) for name, prop in (schema.get("properties") or {}).items()
    }


def test_openai_compatible_proxy_rejecting_response_format_retries_plain_json(monkeypatch):
    """M27: a strict OpenAI-compatible proxy that 400s on response_format
    must be retried once without it — a user's own endpoint keeps working."""
    payloads = []
    calls = {"count": 0}

    def fake_post(url, json=None, headers=None, timeout=None):
        payloads.append(dict(json or {}))
        calls["count"] += 1
        if calls["count"] == 1:
            request = httpx.Request("POST", url)
            raise httpx.HTTPStatusError(
                "400 bad request: response_format not supported",
                request=request,
                response=httpx.Response(400, request=request),
            )
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": '{"quote": "abc", "locator": "x"}'}}],
                "usage": {},
            },
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(httpx, "post", fake_post)
    settings = Settings(
        _env_file=None,
        llm_provider="openai_compatible",
        llm_api_key="sk-test-key-000000000000",
        llm_model="test-model",
        llm_base_url="https://example.test/v1",
    )
    result = ProviderLLMClient(settings).generate_structured(
        stage="evidence", prompt="Return evidence.", response_model=EvidenceSpan
    )
    assert result.quote == "abc"
    assert calls["count"] == 2
    # First attempt carried response_format; the retry dropped it.
    assert payloads[0]["response_format"]["type"] == "json_schema"
    assert "response_format" not in payloads[1]


def test_fenced_json_is_stripped_before_parsing(monkeypatch):
    """P1: endpoints that wrap JSON in Markdown fences must still parse."""
    def fake_post(url, json=None, headers=None, timeout=None):
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": '```json\n{"quote": "abc", "locator": "x"}\n```'}}],
                "usage": {},
            },
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(httpx, "post", fake_post)
    settings = Settings(
        _env_file=None,
        llm_provider="deepseek",
        llm_api_key="sk-test-key-000000000000",
        llm_model="deepseek-v4-pro",
        llm_base_url="https://api.deepseek.com",
    )
    result = ProviderLLMClient(settings).generate_structured(
        stage="evidence", prompt="Return evidence.", response_model=EvidenceSpan
    )
    assert result.quote == "abc"
    assert result.locator == "x"


def test_invalid_json_retries_with_validation_feedback(monkeypatch):
    """P1: malformed JSON is corrected once with the error fed back, not
    failed outright."""
    payloads = []
    calls = {"count": 0}

    def fake_post(url, json=None, headers=None, timeout=None):
        payloads.append(dict(json or {}))
        calls["count"] += 1
        content = (
            '{"quote": "abc", "locator": "x"'  # truncated → invalid JSON
            if calls["count"] == 1
            else '{"quote": "abc", "locator": "x"}'
        )
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": content}}], "usage": {}},
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(httpx, "post", fake_post)
    settings = Settings(
        _env_file=None,
        llm_provider="deepseek",
        llm_api_key="sk-test-key-000000000000",
        llm_model="deepseek-v4-pro",
        llm_base_url="https://api.deepseek.com",
    )
    result = ProviderLLMClient(settings).generate_structured(
        stage="evidence", prompt="Return evidence.", response_model=EvidenceSpan
    )
    assert result.quote == "abc"
    assert calls["count"] == 2
    # the retry carries the failed output + validation error as feedback
    messages = payloads[1]["messages"]
    assert messages[-1]["role"] == "user"
    assert "failed validation" in messages[-1]["content"]
    assert messages[-2]["role"] == "assistant"
    assert "locator" in messages[-2]["content"]


def test_schema_mismatch_retries_with_validation_feedback(monkeypatch):
    """P1: valid JSON that fails the schema is corrected once with the
    pydantic error fed back."""
    payloads = []
    calls = {"count": 0}

    def fake_post(url, json=None, headers=None, timeout=None):
        payloads.append(dict(json or {}))
        calls["count"] += 1
        content = (
            '{"quote": "abc"}'  # missing locator
            if calls["count"] == 1
            else '{"quote": "abc", "locator": "x"}'
        )
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": content}}], "usage": {}},
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(httpx, "post", fake_post)
    settings = Settings(
        _env_file=None,
        llm_provider="deepseek",
        llm_api_key="sk-test-key-000000000000",
        llm_model="deepseek-v4-pro",
        llm_base_url="https://api.deepseek.com",
    )
    result = ProviderLLMClient(settings).generate_structured(
        stage="evidence", prompt="Return evidence.", response_model=EvidenceSpan
    )
    assert result.locator == "x"
    assert calls["count"] == 2
    assert "Validation error" in payloads[1]["messages"][-1]["content"]


def test_parse_failure_exhausts_retries(monkeypatch):
    """P1: if the corrected retry is also invalid, the stage fails with the
    last validation error."""
    def fake_post(url, json=None, headers=None, timeout=None):
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "not json at all"}}], "usage": {}},
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(httpx, "post", fake_post)
    settings = Settings(
        _env_file=None,
        llm_provider="deepseek",
        llm_api_key="sk-test-key-000000000000",
        llm_model="deepseek-v4-pro",
        llm_base_url="https://api.deepseek.com",
        llm_max_retries=1,
    )
    with pytest.raises(RuntimeError, match="llm_provider_failed:evidence"):
        ProviderLLMClient(settings).generate_structured(
            stage="evidence", prompt="Return evidence.", response_model=EvidenceSpan
        )


def test_openai_provider_uses_reasoning_model_token_fields():
    settings = Settings(
        _env_file=None,
        llm_provider="openai",
        llm_api_key="sk-test-key-000000000000",
        llm_model="gpt-5.6-terra",
        llm_base_url="https://api.openai.com/v1",
        llm_reasoning_effort="medium",
    )

    payload = ProviderLLMClient(settings).build_payload(
        stage="evidence", prompt="Return evidence.", response_model=EvidenceSpan
    )

    assert payload["model"] == "gpt-5.6-terra"
    assert payload["max_completion_tokens"] == 4096
    assert payload["reasoning_effort"] == "medium"
    assert "max_tokens" not in payload


def test_provider_retries_transient_503(monkeypatch):
    settings = Settings(
        _env_file=None,
        llm_provider="deepseek",
        llm_api_key="sk-test-key-000000000000",
        llm_model="deepseek-v4-pro",
        llm_base_url="https://api.deepseek.com",
        llm_max_retries=1,
        llm_retry_backoff_seconds=0,
    )
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
                "choices": [
                    {
                        "message": {
                            "content": '{"quote":"exact","locator":"p:1"}'
                        }
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            },
        )

    monkeypatch.setattr("radar.llm.provider.httpx.post", fake_post)
    client = ProviderLLMClient(settings)
    result = client.generate_structured(
        stage="evidence", prompt="Return evidence.", response_model=EvidenceSpan
    )

    assert result.quote == "exact"
    assert len(calls) == 2
    assert client.last_receipt["attempts"] == 2


def _retry_test_settings(**overrides) -> Settings:
    return Settings(
        _env_file=None,
        llm_provider="deepseek",
        llm_api_key="sk-test-key-000000000000",
        llm_model="deepseek-v4-pro",
        llm_base_url="https://api.deepseek.com",
        llm_max_retries=2,
        llm_retry_backoff_seconds=0,
        **overrides,
    )


def test_provider_retries_schema_validation_errors_once_with_feedback(monkeypatch):
    """P1: a schema-invalid response is retried ONCE with the validation
    error fed back; if the correction is also invalid, the stage fails."""
    calls = []

    def fake_post(url, **kwargs):
        calls.append(url)
        request = httpx.Request("POST", url)
        return httpx.Response(
            200,
            request=request,
            json={"choices": [{"message": {"content": '{"unexpected": true}'}}]},
        )

    monkeypatch.setattr("radar.llm.provider.httpx.post", fake_post)
    client = ProviderLLMClient(_retry_test_settings())
    with pytest.raises(RuntimeError, match="llm_provider_failed:evidence"):
        client.generate_structured(
            stage="evidence", prompt="Return evidence.", response_model=EvidenceSpan
        )
    # one feedback retry, then failure — never an unbounded retry loop
    assert len(calls) == 2


def test_provider_does_not_retry_client_errors_other_than_429(monkeypatch):
    calls = []

    def fake_post(url, **kwargs):
        calls.append(url)
        request = httpx.Request("POST", url)
        return httpx.Response(400, request=request)

    monkeypatch.setattr("radar.llm.provider.httpx.post", fake_post)
    client = ProviderLLMClient(_retry_test_settings())
    with pytest.raises(RuntimeError, match="llm_provider_failed:evidence"):
        client.generate_structured(
            stage="evidence", prompt="Return evidence.", response_model=EvidenceSpan
        )
    assert len(calls) == 1
