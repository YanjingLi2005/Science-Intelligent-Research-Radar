"""OpenAI-compatible `/embeddings` adapter with retries and local caching."""

import hashlib
import time
from collections.abc import Callable

import httpx
import numpy as np

MAX_RETRIES = 3


class OpenAICompatEmbeddingClient:
    """Embedding client for hosted OpenAI-compatible HTTP APIs.

    Speaks the ``POST {base_url}/embeddings`` convention shared by OpenAI,
    SiliconFlow, Jina and similar providers, authenticating with a Bearer
    token and sending the whole batch in one ``input`` list.
    """

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        api_key: str,
        timeout_seconds: float = 60.0,
        max_retries: int = MAX_RETRIES,
        post: Callable = httpx.post,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self._post = post
        self._sleep = sleep
        self._cache: dict[str, np.ndarray] = {}
        self.dimension: int | None = None

    @staticmethod
    def _cache_key(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def _post_with_retries(self, texts: list[str]) -> np.ndarray:
        """POST one batch, retrying 429/5xx with exponential backoff."""

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }
        delay = 1.0
        for attempt in range(self.max_retries + 1):
            try:
                response = self._post(
                    f"{self.base_url}/embeddings",
                    json={"model": self.model, "input": texts},
                    headers=headers,
                    timeout=self.timeout_seconds,
                )
                response.raise_for_status()
                payload = response.json()
                data = payload["data"]
                # Providers normally return rows in input order with matching
                # index fields; sort when present so a shuffled response
                # cannot silently scramble the batch.
                if data and all("index" in row for row in data):
                    data = sorted(data, key=lambda row: row["index"])
                return np.asarray(
                    [row["embedding"] for row in data], dtype=np.float32
                )
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                if (status != 429 and status < 500) or attempt == self.max_retries:
                    raise
                self._sleep(delay)
                delay *= 2
        raise AssertionError("unreachable")

    def embed(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.empty((0, self.dimension or 0), dtype=np.float32)
        normalized_texts = [" ".join(text.split()) for text in texts]
        if any(not text for text in normalized_texts):
            raise ValueError("embedding_text_blank")

        missing_texts: list[str] = []
        missing_keys: list[str] = []
        for text in normalized_texts:
            key = self._cache_key(text)
            if key not in self._cache and key not in missing_keys:
                missing_texts.append(text)
                missing_keys.append(key)

        if missing_texts:
            try:
                vectors = self._post_with_retries(missing_texts)
            except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
                raise RuntimeError(f"embedding_provider_failed:openai:{exc}") from exc

            if vectors.ndim != 2 or vectors.shape[0] != len(missing_texts):
                raise RuntimeError("embedding_provider_failed:openai:invalid_shape")
            if vectors.shape[1] == 0 or not np.isfinite(vectors).all():
                raise RuntimeError("embedding_provider_failed:openai:invalid_values")
            norms = np.linalg.norm(vectors, axis=1, keepdims=True)
            if np.any(norms == 0):
                raise RuntimeError("embedding_provider_failed:openai:zero_vector")
            vectors = vectors / norms
            if self.dimension is not None and vectors.shape[1] != self.dimension:
                raise RuntimeError("embedding_provider_failed:openai:dimension_changed")
            self.dimension = int(vectors.shape[1])
            for key, vector in zip(missing_keys, vectors, strict=True):
                self._cache[key] = vector

        result = np.stack(
            [self._cache[self._cache_key(text)] for text in normalized_texts]
        )
        return result.astype(np.float32, copy=False)
