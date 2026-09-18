"""Create the configured embedding client without hiding missing settings."""

from radar.config import Settings
from radar.embeddings.base import EmbeddingClient
from radar.embeddings.ollama import OllamaEmbeddingClient
from radar.embeddings.openai_compat import OpenAICompatEmbeddingClient


def configured_embedding_client(settings: Settings) -> EmbeddingClient | None:
    provider = (settings.embedding_provider or "").strip().lower()
    if not provider:
        return None
    if provider == "ollama":
        if not settings.embedding_model or not settings.embedding_base_url:
            raise RuntimeError("embedding_not_configured:ollama")
        return OllamaEmbeddingClient(
            base_url=settings.embedding_base_url,
            model=settings.embedding_model,
            api_key=settings.embedding_api_key,
        )
    if provider in {"openai", "openai_compatible"}:
        if (
            not settings.embedding_api_key
            or not settings.embedding_model
            or not settings.embedding_base_url
        ):
            raise RuntimeError("embedding_not_configured:openai")
        return OpenAICompatEmbeddingClient(
            base_url=settings.embedding_base_url,
            model=settings.embedding_model,
            api_key=settings.embedding_api_key,
            timeout_seconds=settings.embedding_timeout_seconds,
            max_retries=settings.embedding_max_retries,
        )
    raise RuntimeError(f"embedding_provider_unsupported:{provider}")
