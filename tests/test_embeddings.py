import httpx
import numpy as np
import pytest

from radar.embeddings.ollama import OllamaEmbeddingClient


def test_ollama_embedding_validates_normalizes_and_caches():
    calls = []

    def post(url, *, json, headers, timeout):
        calls.append(json)
        vectors = [[3.0, 4.0] if text == "alpha" else [0.0, 2.0] for text in json["input"]]
        return httpx.Response(
            200,
            json={"model": json["model"], "embeddings": vectors},
            request=httpx.Request("POST", url),
        )

    client = OllamaEmbeddingClient(
        base_url="http://127.0.0.1:11434",
        model="qwen3-embedding:0.6b",
        post=post,
    )
    first = client.embed(["alpha", "beta"])
    second = client.embed(["alpha"])

    assert first.shape == (2, 2)
    assert np.allclose(np.linalg.norm(first, axis=1), [1.0, 1.0])
    assert np.allclose(second[0], first[0])
    assert client.dimension == 2
    assert len(calls) == 1


def test_ollama_embedding_rejects_invalid_shape():
    def post(url, *, json, headers, timeout):
        return httpx.Response(
            200,
            json={"embeddings": [[1.0, 2.0]]},
            request=httpx.Request("POST", url),
        )

    client = OllamaEmbeddingClient(
        base_url="http://127.0.0.1:11434",
        model="qwen3-embedding:0.6b",
        post=post,
    )
    with pytest.raises(RuntimeError, match="invalid_shape"):
        client.embed(["alpha", "beta"])


from radar.config import Settings
from radar.embeddings.factory import configured_embedding_client
from radar.embeddings.openai_compat import OpenAICompatEmbeddingClient


def _openai_response(url, vectors):
    return httpx.Response(
        200,
        json={
            "object": "list",
            "data": [
                {"object": "embedding", "index": index, "embedding": vector}
                for index, vector in enumerate(vectors)
            ],
        },
        request=httpx.Request("POST", url),
    )


def test_openai_embedding_validates_normalizes_and_caches():
    calls = []

    def post(url, *, json, headers, timeout):
        calls.append((url, json, headers))
        vectors = [[3.0, 4.0] if text == "alpha" else [0.0, 2.0] for text in json["input"]]
        return _openai_response(url, vectors)

    client = OpenAICompatEmbeddingClient(
        base_url="https://api.openai.com/v1/",
        model="text-embedding-3-small",
        api_key="sk-test-key-000000000000",
        post=post,
        sleep=lambda _: None,
    )
    first = client.embed(["alpha", "beta"])
    second = client.embed(["alpha"])

    assert first.shape == (2, 2)
    assert np.allclose(np.linalg.norm(first, axis=1), [1.0, 1.0])
    assert np.allclose(second[0], first[0])
    assert client.dimension == 2
    assert len(calls) == 1
    url, payload, headers = calls[0]
    assert url == "https://api.openai.com/v1/embeddings"
    assert payload == {"model": "text-embedding-3-small", "input": ["alpha", "beta"]}
    assert headers["Authorization"] == "Bearer sk-test-key-000000000000"


def test_openai_embedding_retries_429_then_succeeds():
    attempts = []

    def post(url, *, json, headers, timeout):
        attempts.append(len(attempts))
        if len(attempts) < 3:
            return httpx.Response(
                429, request=httpx.Request("POST", url)
            )
        return _openai_response(url, [[1.0, 0.0] for _ in json["input"]])

    client = OpenAICompatEmbeddingClient(
        base_url="https://api.siliconflow.cn/v1",
        model="BAAI/bge-m3",
        api_key="sk-test-key-000000000000",
        post=post,
        sleep=lambda _: None,
    )
    vectors = client.embed(["alpha"])

    assert vectors.shape == (1, 2)
    assert len(attempts) == 3


def test_openai_embedding_gives_up_after_max_retries():
    def post(url, *, json, headers, timeout):
        return httpx.Response(500, request=httpx.Request("POST", url))

    client = OpenAICompatEmbeddingClient(
        base_url="https://api.openai.com/v1",
        model="text-embedding-3-small",
        api_key="sk-test-key-000000000000",
        max_retries=2,
        post=post,
        sleep=lambda _: None,
    )
    with pytest.raises(RuntimeError, match="embedding_provider_failed:openai"):
        client.embed(["alpha"])


def test_openai_embedding_does_not_retry_client_errors():
    calls = []

    def post(url, *, json, headers, timeout):
        calls.append(url)
        return httpx.Response(
            401, json={"error": {"message": "bad key"}},
            request=httpx.Request("POST", url),
        )

    client = OpenAICompatEmbeddingClient(
        base_url="https://api.openai.com/v1",
        model="text-embedding-3-small",
        api_key="sk-bad",
        post=post,
        sleep=lambda _: None,
    )
    with pytest.raises(RuntimeError, match="embedding_provider_failed:openai"):
        client.embed(["alpha"])
    assert len(calls) == 1


def test_openai_embedding_rejects_malformed_payload():
    def post(url, *, json, headers, timeout):
        return httpx.Response(
            200, json={"unexpected": True},
            request=httpx.Request("POST", url),
        )

    client = OpenAICompatEmbeddingClient(
        base_url="https://api.openai.com/v1",
        model="text-embedding-3-small",
        api_key="sk-test-key-000000000000",
        post=post,
        sleep=lambda _: None,
    )
    with pytest.raises(RuntimeError, match="embedding_provider_failed:openai"):
        client.embed(["alpha"])


def _settings(**overrides) -> Settings:
    values = {
        "embedding_provider": None,
        "embedding_api_key": None,
        "embedding_model": None,
        "embedding_base_url": None,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def test_factory_returns_none_without_provider():
    assert configured_embedding_client(_settings()) is None


def test_factory_builds_ollama_client():
    client = configured_embedding_client(
        _settings(
            embedding_provider="ollama",
            embedding_model="qwen3-embedding:0.6b",
            embedding_base_url="http://127.0.0.1:11434",
        )
    )
    assert isinstance(client, OllamaEmbeddingClient)


def test_factory_ollama_requires_model_and_base_url():
    with pytest.raises(RuntimeError, match="embedding_not_configured:ollama"):
        configured_embedding_client(_settings(embedding_provider="ollama"))


@pytest.mark.parametrize("provider", ["openai", "openai_compatible", "OpenAI"])
def test_factory_builds_openai_client(provider):
    client = configured_embedding_client(
        _settings(
            embedding_provider=provider,
            embedding_api_key="sk-test-key-000000000000",
            embedding_model="text-embedding-3-small",
            embedding_base_url="https://api.openai.com/v1",
        )
    )
    assert isinstance(client, OpenAICompatEmbeddingClient)
    assert client.timeout_seconds == 60.0


def test_factory_openai_requires_key_model_and_base_url():
    with pytest.raises(RuntimeError, match="embedding_not_configured:openai"):
        configured_embedding_client(_settings(embedding_provider="openai"))
    with pytest.raises(RuntimeError, match="embedding_not_configured:openai"):
        configured_embedding_client(
            _settings(
                embedding_provider="openai",
                embedding_api_key="sk-test-key-000000000000",
                embedding_model="text-embedding-3-small",
            )
        )


def test_factory_rejects_unknown_provider():
    with pytest.raises(RuntimeError, match="embedding_provider_unsupported:deepseek"):
        configured_embedding_client(_settings(embedding_provider="deepseek"))
