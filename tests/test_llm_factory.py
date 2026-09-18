"""Selection logic of the shared analysis-LLM factory."""

import sys
from pathlib import Path

from radar.config import Settings
from radar.llm.factory import build_analysis_llm, describe_llm_setup
from radar.llm.ollama import OllamaLLMClient
from radar.llm.provider import ProviderLLMClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from verify_live_stack import check_configuration  # noqa: E402


def _settings(**overrides) -> Settings:
    return Settings(_env_file=None, **overrides)


REMOTE = {
    "llm_api_key": "sk-test-key-000000000000",
    "llm_model": "deepseek-chat",
    "llm_base_url": "https://api.deepseek.com",
}


def test_local_model_wins_over_remote():
    client = build_analysis_llm(_settings(local_llm_model="qwen3:4b", **REMOTE))
    assert isinstance(client, OllamaLLMClient)
    assert client.model_name == "qwen3:4b"


def test_remote_built_only_when_triple_complete():
    client = build_analysis_llm(_settings(**REMOTE))
    assert isinstance(client, ProviderLLMClient)


def test_none_when_nothing_or_partial_remote_configured():
    assert build_analysis_llm(_settings()) is None
    assert build_analysis_llm(_settings(llm_api_key="sk-test-key-000000000000")) is None
    assert (
        build_analysis_llm(
            _settings(llm_model="deepseek-chat", llm_base_url="https://api.deepseek.com")
        )
        is None
    )


def test_describe_setup_local():
    setup = describe_llm_setup(_settings(local_llm_model="qwen3:4b", **REMOTE))
    assert setup == {
        "configured": True,
        "mode": "local",
        "model": "qwen3:4b",
        "provider": "ollama",
        "base_url": "http://127.0.0.1:11434",
        "missing": [],
    }


def test_describe_setup_remote():
    setup = describe_llm_setup(_settings(**REMOTE))
    assert setup["configured"] is True
    assert setup["mode"] == "remote"
    assert setup["model"] == "deepseek-chat"
    assert setup["missing"] == []


def test_describe_setup_missing_lists_absent_remote_vars():
    setup = describe_llm_setup(_settings())
    assert setup["configured"] is False
    assert setup["mode"] is None
    assert setup["model"] is None
    assert setup["missing"] == ["LLM_API_KEY", "LLM_MODEL", "LLM_BASE_URL"]

    partial = describe_llm_setup(_settings(llm_model="deepseek-chat"))
    assert partial["configured"] is False
    assert partial["missing"] == ["LLM_API_KEY", "LLM_BASE_URL"]


def test_verify_script_configuration_check_matches_factory():
    ok, message = check_configuration(_settings())
    assert ok is False
    assert "LLM_API_KEY" in message

    ok, _ = check_configuration(_settings(**REMOTE))
    assert ok is True

    ok, message = check_configuration(_settings(local_llm_model="qwen3:4b"))
    assert ok is True
    assert "qwen3:4b" in message
