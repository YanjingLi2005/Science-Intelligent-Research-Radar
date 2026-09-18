"""Ollama `/api/embed` adapter with validation and process-local caching."""

import hashlib
from collections.abc import Callable

import httpx
import numpy as np


class OllamaEmbeddingClient:
    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        api_key: str | None = None,
        timeout_seconds: float = 60.0,
        post: Callable = httpx.post,
    ):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds
        self._post = post
        self._cache: dict[str, np.ndarray] = {}
        self.dimension: int | None = None

    @staticmethod
    def _cache_key(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

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
            headers = {"Content-Type": "application/json"}
            if self.api_key:
                headers["Authorization"] = f"Bearer {self.api_key}"
            try:
                response = self._post(
                    f"{self.base_url}/api/embed",
                    json={
                        "model": self.model,
                        "input": missing_texts,
                        "truncate": True,
                    },
                    headers=headers,
                    timeout=self.timeout_seconds,
                )
                response.raise_for_status()
                payload = response.json()
                vectors = np.asarray(payload["embeddings"], dtype=np.float32)
            except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
                raise RuntimeError(f"embedding_provider_failed:ollama:{exc}") from exc

            if vectors.ndim != 2 or vectors.shape[0] != len(missing_texts):
                raise RuntimeError("embedding_provider_failed:ollama:invalid_shape")
            if vectors.shape[1] == 0 or not np.isfinite(vectors).all():
                raise RuntimeError("embedding_provider_failed:ollama:invalid_values")
            norms = np.linalg.norm(vectors, axis=1, keepdims=True)
            if np.any(norms == 0):
                raise RuntimeError("embedding_provider_failed:ollama:zero_vector")
            vectors = vectors / norms
            if self.dimension is not None and vectors.shape[1] != self.dimension:
                raise RuntimeError("embedding_provider_failed:ollama:dimension_changed")
            self.dimension = int(vectors.shape[1])
            for key, vector in zip(missing_keys, vectors, strict=True):
                self._cache[key] = vector

        result = np.stack(
            [self._cache[self._cache_key(text)] for text in normalized_texts]
        )
        return result.astype(np.float32, copy=False)
