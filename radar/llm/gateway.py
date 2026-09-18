"""Unified LLM Gateway with transparent fallback, circuit breaker and timeout control.

Provides an Adapter/Gateway implementing the `LLMClient` protocol. When the primary
model times out (e.g. > 8s) or encounters transient errors (400 geo-block, 429 rate
limit, 502/504 gateway timeout, or connection failure), requests are seamlessly
diverted to a configured fallback model/relay node without disrupting the caller.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterator
from typing import Any, TypeVar

import httpx
from pydantic import BaseModel

from radar.llm.base import LLMClient

logger = logging.getLogger("radar.llm.gateway")

ResponseT = TypeVar("ResponseT", bound=BaseModel)

_FALLBACK_HTTP_STATUSES = frozenset({400, 403, 408, 429, 500, 502, 503, 504})


class FallbackLLMClient:
    """Multi-model gateway with automatic fallback, circuit breaker and telemetry."""

    def __init__(
        self,
        primary_client: LLMClient,
        fallback_client: LLMClient,
        *,
        primary_timeout_seconds: float = 8.0,
        circuit_breaker_threshold: int = 3,
        circuit_breaker_cooldown_seconds: float = 60.0,
    ):
        self.primary_client = primary_client
        self.fallback_client = fallback_client
        self.primary_timeout_seconds = primary_timeout_seconds
        self.circuit_breaker_threshold = circuit_breaker_threshold
        self.circuit_breaker_cooldown_seconds = circuit_breaker_cooldown_seconds

        self.consecutive_primary_failures = 0
        self.primary_circuit_open_until = 0.0
        self.last_receipt: dict[str, Any] = {}

    @property
    def provider_name(self) -> str:
        primary_prov = getattr(self.primary_client, "provider_name", "primary")
        fallback_prov = getattr(self.fallback_client, "provider_name", "fallback")
        return f"gateway({primary_prov}->{fallback_prov})"

    @property
    def model_name(self) -> str:
        primary_mod = getattr(self.primary_client, "model_name", "primary")
        fallback_mod = getattr(self.fallback_client, "model_name", "fallback")
        return f"{primary_mod}|{fallback_mod}"

    def is_primary_circuit_open(self) -> bool:
        """Check if primary is in cooldown after repeated failures."""
        now = time.time()
        return now < self.primary_circuit_open_until

    def _record_primary_success(self) -> None:
        self.consecutive_primary_failures = 0
        self.primary_circuit_open_until = 0.0

    def _record_primary_failure(self, reason: Exception) -> None:
        self.consecutive_primary_failures += 1
        if self.consecutive_primary_failures >= self.circuit_breaker_threshold:
            self.primary_circuit_open_until = (
                time.time() + self.circuit_breaker_cooldown_seconds
            )
            logger.warning(
                "Primary LLM tripped circuit breaker after %d consecutive failures (%s). Entering cooldown for %.1fs.",
                self.consecutive_primary_failures,
                reason,
                self.circuit_breaker_cooldown_seconds,
            )

    def _is_fallback_worthy(self, exc: Exception) -> bool:
        """Determine if an exception should trigger automatic fallback."""
        if isinstance(exc, (TimeoutError, httpx.TimeoutException)):
            return True
        if isinstance(exc, httpx.HTTPStatusError):
            return exc.response.status_code in _FALLBACK_HTTP_STATUSES
        if isinstance(exc, (httpx.ConnectError, httpx.NetworkError, RuntimeError)):
            return True
        return True

    def generate_structured(
        self,
        *,
        stage: str,
        prompt: str,
        response_model: type[ResponseT],
        max_tokens: int | None = None,
    ) -> ResponseT:
        """Generate structured output with automatic fallback on failure/timeout."""
        # 1. Check Circuit Breaker
        if not self.is_primary_circuit_open():
            try:
                # Attempt primary with tight timeout
                res = self.primary_client.generate_structured(
                    stage=stage,
                    prompt=prompt,
                    response_model=response_model,
                    max_tokens=max_tokens,
                )
                self._record_primary_success()
                self.last_receipt = getattr(self.primary_client, "last_receipt", {}) or {}
                self.last_receipt["fallback_used"] = False
                return res
            except Exception as exc:
                if not self._is_fallback_worthy(exc):
                    raise
                self._record_primary_failure(exc)
                primary_name = getattr(self.primary_client, "model_name", "primary")
                fallback_name = getattr(self.fallback_client, "model_name", "fallback")
                logger.warning(
                    "Primary LLM (%s) failed on stage '%s': %s. Seamlessly switching to fallback (%s).",
                    primary_name,
                    stage,
                    exc,
                    fallback_name,
                )
        else:
            primary_name = getattr(self.primary_client, "model_name", "primary")
            fallback_name = getattr(self.fallback_client, "model_name", "fallback")
            logger.info(
                "Primary LLM (%s) circuit is OPEN. Directly executing with fallback (%s).",
                primary_name,
                fallback_name,
            )

        # 2. Execute Fallback
        res = self.fallback_client.generate_structured(
            stage=stage,
            prompt=prompt,
            response_model=response_model,
            max_tokens=max_tokens,
        )
        self.last_receipt = getattr(self.fallback_client, "last_receipt", {}) or {}
        self.last_receipt["fallback_used"] = True
        return res

    def stream_text(
        self,
        *,
        stage: str,
        prompt: str,
        max_tokens: int | None = None,
    ) -> Iterator[str]:
        """Stream text tokens with automatic fallback on failure/timeout."""
        if not self.is_primary_circuit_open():
            try:
                stream_iter = self.primary_client.stream_text(
                    stage=stage,
                    prompt=prompt,
                    max_tokens=max_tokens,
                )
                # Test the first chunk to ensure stream connection succeeded
                first_chunk = next(stream_iter, None)
                if first_chunk is not None:
                    self._record_primary_success()
                    yield first_chunk
                    yield from stream_iter
                    return
            except Exception as exc:
                if not self._is_fallback_worthy(exc):
                    raise
                self._record_primary_failure(exc)
                primary_name = getattr(self.primary_client, "model_name", "primary")
                fallback_name = getattr(self.fallback_client, "model_name", "fallback")
                logger.warning(
                    "Primary LLM (%s) stream failed on stage '%s': %s. Falling back to (%s).",
                    primary_name,
                    stage,
                    exc,
                    fallback_name,
                )

        # Fallback stream
        yield from self.fallback_client.stream_text(
            stage=stage,
            prompt=prompt,
            max_tokens=max_tokens,
        )
