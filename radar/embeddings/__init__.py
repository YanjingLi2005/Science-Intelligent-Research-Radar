"""Embedding adapters used by hybrid retrieval."""

from radar.embeddings.base import EmbeddingClient
from radar.embeddings.ollama import OllamaEmbeddingClient
from radar.embeddings.openai_compat import OpenAICompatEmbeddingClient

__all__ = ["EmbeddingClient", "OllamaEmbeddingClient", "OpenAICompatEmbeddingClient"]
