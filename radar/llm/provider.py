"""OpenAI-compatible structured JSON adapter with explicit errors."""

import json
import re
import time
from typing import TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from radar.config import Settings, get_settings
from radar.tracing import get_trace_id

_FENCE_RE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL)

# Stages that copy verbatim spans (or large manuscript text) into JSON. High-
# effort hidden reasoning burns the whole output budget on reasoning_content:
# DeepSeek returns finish_reason="length" with empty/truncated content, the JSON
# fails to parse, and claim extraction silently degrades to the heuristic
# fallback (0 claims). These stages run without thinking so the token budget
# goes to the JSON itself.
_EXTRACTION_STAGES = {
    "claim_extraction",
    "claim_candidate_detection",
    "manuscript_overview",
    "manuscript_understanding",
}

# Extraction stages get this output-cap floor so a very long manuscript's JSON
# (up to the schema's candidate limit) fits in a single attempt instead of
# relying on a truncation-triggered escalation.
_EXTRACTION_MAX_TOKENS = 16_384


def _strip_fences(content: str) -> str:
    """Remove Markdown code fences some endpoints wrap JSON in."""
    stripped = content.strip()
    match = _FENCE_RE.match(stripped)
    if match:
        return match.group(1).strip()
    return stripped


def _with_parse_feedback(payload: dict, content: str, exc: Exception) -> dict:
    """Instructor-style retry: append the failed output and the validation
    error to the conversation so the model can correct a near-valid response
    instead of the whole stage dying."""
    messages = list(payload.get("messages", []))
    messages.extend(
        [
            {"role": "assistant", "content": content[:4000]},
            {
                "role": "user",
                "content": (
                    "Your previous output failed validation. Fix it and return "
                    f"valid JSON matching the schema. Validation error: {str(exc)[:1500]}"
                ),
            },
        ]
    )
    updated = dict(payload)
    updated["messages"] = messages
    return updated


ResponseT = TypeVar("ResponseT", bound=BaseModel)


def _strict_json_schema(model: type[BaseModel]) -> dict:
    """Return a JSON Schema that OpenAI strict ``json_schema`` mode accepts.

    OpenAI strict mode requires, at every object level: ``additionalProperties:
    false``, every property listed in ``required``, no null-type alternatives
    in unions, and no ``default`` values. Pydantic's ``model_json_schema()``
    violates all four for optional fields, so OpenAI rejects the raw schema
    with HTTP 400 ``invalid_json_schema``. DeepSeek's ``json_object`` mode has
    no such requirement; this transform is applied only to the json_schema
    branch. Optional fields are kept in ``required`` (the model always emits
    them, e.g. ``""`` / ``[]`` when it has nothing), which pydantic parses
    into the field's default.
    """

    def _harden(node: dict) -> None:
        node.pop("default", None)
        if node.get("type") == "object":
            properties = node.get("properties") or {}
            node["additionalProperties"] = False
            node["required"] = sorted(set(node.get("required", [])) | set(properties))
            for prop in properties.values():
                _harden(prop)
        elif node.get("type") == "array":
            items = node.get("items")
            if isinstance(items, dict):
                _harden(items)
        for key in ("anyOf", "oneOf", "allOf"):
            alternatives = node.get(key)
            if not isinstance(alternatives, list):
                continue
            pruned = [
                alt
                for alt in alternatives
                if not (isinstance(alt, dict) and alt.get("type") == "null")
            ]
            for alt in pruned:
                _harden(alt)
            if not pruned:
                node.pop(key, None)
            elif key in ("anyOf", "oneOf") and len(pruned) == 1:
                # Single surviving branch (e.g. Optional[X]): inline it.
                node.pop(key)
                node.update(pruned[0])
            else:
                node[key] = pruned

    schema = model.model_json_schema()
    for definition in (schema.get("$defs") or {}).values():
        _harden(definition)
    _harden(schema)
    return schema


class ProviderLLMClient:
    transient_statuses = {429, 500, 502, 503, 504}

    def __init__(
        self,
        settings: Settings | None = None,
        timeout_seconds: float | None = None,
    ):
        self.settings = settings or get_settings()
        self.timeout_seconds = timeout_seconds or self.settings.llm_timeout_seconds
        self.last_receipt: dict = {}

    def generate_structured(
        self,
        *,
        stage: str,
        prompt: str,
        response_model: type[ResponseT],
        max_tokens: int | None = None,
    ) -> ResponseT:
        if not all([self.settings.llm_api_key, self.settings.llm_model, self.settings.llm_base_url]):
            raise RuntimeError("llm_not_configured")
        payload = self.build_payload(
            stage=stage, prompt=prompt, response_model=response_model,
            max_tokens=max_tokens,
        )
        last_error: Exception | None = None
        format_dropped = False
        parse_feedback_used = False
        for attempt in range(self.settings.llm_max_retries + 1):
            started = time.perf_counter()
            try:
                response = httpx.post(
                    f"{self.settings.llm_base_url.rstrip('/')}/chat/completions",
                    json=payload,
                    headers={"Authorization": f"Bearer {self.settings.llm_api_key}"},
                    timeout=self.timeout_seconds,
                )
                response.raise_for_status()
                body = response.json()
                content = body["choices"][0]["message"]["content"]
                finish_reason = body["choices"][0].get("finish_reason")
                if finish_reason == "length":
                    # finish_reason=="length" means the model hit the output cap
                    # mid-response: hidden reasoning burning the budget, or a
                    # real JSON response truncated mid-string (Unterminated
                    # string). Either way the output is incomplete. A long
                    # manuscript can legitimately need far more than
                    # llm_max_tokens, so keep doubling the cap until the output
                    # completes or the practical ceiling is reached.
                    token_key = (
                        "max_completion_tokens"
                        if "max_completion_tokens" in payload
                        else "max_tokens"
                    )
                    current_cap = int(payload.get(token_key, self.settings.llm_max_tokens))
                    if current_cap < 32_768:
                        payload[token_key] = min(current_cap * 2, 32_768)
                        continue
                usage = body.get("usage") or {}
                self.last_receipt = {
                    "raw_response": content,
                    "usage": {
                        **usage,
                        # OpenAI-compatible usage, falling back to Ollama-style
                        # eval counters when the endpoint reports those instead.
                        "prompt_tokens": int(
                            usage.get(
                                "prompt_tokens", body.get("prompt_eval_count", 0)
                            )
                            or 0
                        ),
                        "completion_tokens": int(
                            usage.get("completion_tokens", body.get("eval_count", 0))
                            or 0
                        ),
                    },
                    "latency_ms": int((time.perf_counter() - started) * 1000),
                    "attempts": attempt + 1,
                    "self_correction_attempts": 1 if parse_feedback_used else 0,
                    "trace_id": get_trace_id(),
                }
                content = _strip_fences(content)
                try:
                    parsed = json.loads(content)
                    return response_model.model_validate(parsed)
                except (ValidationError, ValueError) as exc:
                    last_error = exc
                    if not parse_feedback_used and attempt < self.settings.llm_max_retries:
                        # Instructor-style: a near-valid response (bad field,
                        # trailing junk, fenced JSON) is corrected once with
                        # the validation error fed back, not failed outright.
                        parse_feedback_used = True
                        payload = _with_parse_feedback(payload, content, exc)
                        continue
                    retryable = False
            except httpx.HTTPStatusError as exc:
                last_error = exc
                # 4xx other than 429 is a request/schema problem: retrying
                # the same prompt cannot fix it.
                retryable = exc.response.status_code in self.transient_statuses
                if (
                    not retryable
                    and exc.response.status_code == 400
                    and not format_dropped
                    and payload.get("response_format", {}).get("type") == "json_schema"
                ):
                    # Some OpenAI-compatible proxies reject response_format
                    # entirely. Retry once with plain-JSON prompting so a
                    # user's own endpoint still works; the schema-driven
                    # parse below accepts a plain JSON object either way.
                    payload.pop("response_format", None)
                    format_dropped = True
                    retryable = True
            except httpx.HTTPError as exc:
                # Timeouts and transport-level network failures are transient.
                last_error = exc
                retryable = True
            except (ValidationError, KeyError, TypeError, ValueError) as exc:
                # The response does not match the schema; fail fast instead
                # of burning retries on an unfixable prompt/schema mismatch.
                last_error = exc
                retryable = False
            if not retryable or attempt >= self.settings.llm_max_retries:
                break
            time.sleep(self.settings.llm_retry_backoff_seconds * (2**attempt))
        raise RuntimeError(f"llm_provider_failed:{stage}: {last_error}") from last_error

    def build_payload(
        self,
        *,
        stage: str,
        prompt: str,
        response_model: type[ResponseT],
        max_tokens: int | None = None,
    ) -> dict:
        """Build a provider-specific Chat Completions request without sending it."""

        schema = response_model.model_json_schema()
        token_cap = max_tokens or self.settings.llm_max_tokens
        lean = stage in _EXTRACTION_STAGES
        if lean and max_tokens is None:
            # Long manuscripts can legitimately need far more output than the
            # default llm_max_tokens; give these stages a generous cap so a
            # very long paper isn't truncated mid-JSON on the first attempt.
            token_cap = max(token_cap, _EXTRACTION_MAX_TOKENS)
        effective_model = self.settings.llm_model
        provider = (self.settings.llm_provider or "").strip().lower()
        if provider.startswith("deepseek"):
            properties = ", ".join(schema.get("properties", {}).keys())
            system_prompt = (
                "Return exactly one valid JSON object and no Markdown. "
                "The JSON object must validate against the supplied JSON Schema. "
                f"Required top-level JSON keys: {properties}.\n"
                f"JSON Schema:\n{json.dumps(schema, ensure_ascii=False)}"
            )
            return {
                "model": effective_model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": prompt},
                ],
                # DeepSeek JSON Output supports json_object rather than
                # OpenAI's json_schema response format.
                "response_format": {"type": "json_object"},
                "thinking": (
                    {"type": "disabled"}
                    if lean
                    else {"type": self.settings.llm_thinking}
                ),
                "reasoning_effort": (
                    "low" if lean else self.settings.llm_reasoning_effort
                ),
                "max_tokens": token_cap,
                "stream": False,
            }

        payload = {
            "model": effective_model,
            "messages": [{"role": "user", "content": prompt}],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": stage,
                    "strict": True,
                    "schema": _strict_json_schema(response_model),
                },
            },
        }
        if provider == "openai":
            # Current OpenAI reasoning models use max_completion_tokens;
            # max_tokens is deprecated and rejected by some model families.
            payload["max_completion_tokens"] = token_cap
            payload["reasoning_effort"] = (
                "low"
                if stage in _EXTRACTION_STAGES
                else self.settings.llm_reasoning_effort
            )
        else:
            # Preserve broad OpenAI-compatible endpoint compatibility.
            payload["max_tokens"] = token_cap
        return payload

    def stream_text(
        self,
        *,
        stage: str,
        prompt: str,
        max_tokens: int | None = None,
    ):
        """Stream plain text tokens from the configured LLM provider."""
        if not all([self.settings.llm_api_key, self.settings.llm_model, self.settings.llm_base_url]):
            raise RuntimeError("llm_not_configured")

        provider = self.settings.llm_provider.lower()
        token_cap = max_tokens or self.settings.llm_max_tokens

        payload = {
            "model": self.settings.llm_model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": True,
        }
        if provider == "deepseek":
            payload["max_tokens"] = token_cap
            payload["thinking"] = (
                {"type": "disabled"}
                if stage in _EXTRACTION_STAGES
                else {"type": self.settings.llm_thinking}
            )
        elif provider == "openai":
            payload["max_completion_tokens"] = token_cap
        else:
            payload["max_tokens"] = token_cap

        headers = {"Authorization": f"Bearer {self.settings.llm_api_key}"}
        url = f"{self.settings.llm_base_url.rstrip('/')}/chat/completions"

        with httpx.Client(timeout=self.timeout_seconds) as client:
            with client.stream("POST", url, json=payload, headers=headers) as response:
                response.raise_for_status()
                for line in response.iter_lines():
                    line = line.strip()
                    if not line:
                        continue
                    if line.startswith("data: "):
                        data_str = line[6:].strip()
                        if data_str == "[DONE]":
                            break
                        try:
                            chunk_data = json.loads(data_str)
                            choices = chunk_data.get("choices") or []
                            if choices:
                                delta = choices[0].get("delta") or {}
                                content = delta.get("content") or ""
                                if content:
                                    yield content
                        except Exception:
                            continue

