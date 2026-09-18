"""Local Ollama structured-output client for private manuscript analysis."""

import json
import time
from typing import TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from radar.config import Settings, get_settings
from radar.tracing import get_trace_id


ResponseT = TypeVar("ResponseT", bound=BaseModel)


class OllamaLLMClient:
    provider_name = "ollama"
    transient_statuses = {429, 500, 502, 503, 504}

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self.model_name = self.settings.local_llm_model or ""
        self.last_receipt: dict = {}

    def generate_structured(
        self,
        *,
        stage: str,
        prompt: str,
        response_model: type[ResponseT],
        max_tokens: int | None = None,
    ) -> ResponseT:
        if not self.model_name or not self.settings.local_llm_base_url:
            raise RuntimeError("local_llm_not_configured")
        schema = response_model.model_json_schema()
        system_prompt = (
            "Return exactly one JSON object that validates against this JSON Schema. "
            "Do not include Markdown or commentary.\n"
            + json.dumps(schema, ensure_ascii=False)
        )
        payload = {
            "model": self.model_name,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ],
            "stream": False,
            "think": False,
            "format": schema,
            "options": {
                "temperature": 0,
                "num_ctx": 32_768,
                "num_predict": max_tokens or 2_048,
            },
        }
        last_error: Exception | None = None
        parse_feedback_used = False
        for attempt in range(self.settings.llm_max_retries + 1):
            started = time.perf_counter()
            try:
                response = httpx.post(
                    f"{self.settings.local_llm_base_url.rstrip('/')}/api/chat",
                    json=payload,
                    timeout=self.settings.local_llm_timeout_seconds,
                )
                response.raise_for_status()
                body = response.json()
                content = body["message"]["content"]
                result = response_model.model_validate_json(content)
            except httpx.HTTPStatusError as exc:
                last_error = exc
                retryable = exc.response.status_code in self.transient_statuses
            except httpx.HTTPError as exc:
                # Timeouts and transport-level network failures are transient.
                last_error = exc
                retryable = True
            except (ValidationError, ValueError, KeyError, TypeError) as exc:
                # Schema/content mismatch: retrying cannot fix local prompt grammar.
                last_error = exc
                retryable = False
            else:
                self.last_receipt = {
                    "raw_response": content,
                    "usage": {
                        "prompt_tokens": int(body.get("prompt_eval_count", 0)),
                        "completion_tokens": int(body.get("eval_count", 0)),
                    },
                    "latency_ms": int((time.perf_counter() - started) * 1000),
                    "attempts": attempt + 1,
                    "trace_id": get_trace_id(),
                }
                return result
            if not retryable or attempt >= self.settings.llm_max_retries:
                break
            time.sleep(self.settings.llm_retry_backoff_seconds * (2**attempt))
        raise RuntimeError(
            f"ollama_structured_output_failed:{stage}: {last_error}"
        ) from last_error

    def stream_text(
        self,
        *,
        stage: str,
        prompt: str,
        max_tokens: int | None = None,
    ):
        """Stream plain text tokens from local Ollama service."""
        if not self.model_name or not self.settings.local_llm_base_url:
            raise RuntimeError("local_llm_not_configured")

        payload = {
            "model": self.model_name,
            "messages": [
                {"role": "user", "content": prompt},
            ],
            "stream": True,
            "options": {
                "temperature": 0.3,
                "num_ctx": 32_768,
                "num_predict": max_tokens or 2_048,
            },
        }
        url = f"{self.settings.local_llm_base_url.rstrip('/')}/api/chat"
        with httpx.Client(timeout=self.settings.local_llm_timeout_seconds) as client:
            with client.stream("POST", url, json=payload) as response:
                response.raise_for_status()
                for line in response.iter_lines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        chunk = json.loads(line)
                        content = chunk.get("message", {}).get("content", "")
                        if content:
                            yield content
                    except Exception:
                        continue

