"""Single entry point for building the analysis LLM from settings.

Every service that needs a structured-output model goes through here so the
local-first policy (``LOCAL_LLM_MODEL`` beats any remote provider) is decided
in exactly one place.
"""

from radar.config import Settings, get_settings
from radar.llm.base import LLMClient
from radar.llm.gateway import FallbackLLMClient
from radar.llm.ollama import OllamaLLMClient
from radar.llm.provider import ProviderLLMClient


# Remote structured-output calls are only usable with all three settings.
REMOTE_REQUIRED_FIELDS = {
    "llm_api_key": "LLM_API_KEY",
    "llm_model": "LLM_MODEL",
    "llm_base_url": "LLM_BASE_URL",
}

FALLBACK_REQUIRED_FIELDS = {
    "llm_fallback_api_key": "LLM_FALLBACK_API_KEY",
    "llm_fallback_model": "LLM_FALLBACK_MODEL",
    "llm_fallback_base_url": "LLM_FALLBACK_BASE_URL",
}


def _build_fallback_settings(settings: Settings) -> Settings:
    data = settings.model_dump()
    data["llm_provider"] = settings.llm_fallback_provider or "openai_compatible"
    data["llm_api_key"] = settings.llm_fallback_api_key
    data["llm_model"] = settings.llm_fallback_model
    data["llm_base_url"] = settings.llm_fallback_base_url
    return Settings(**data)


def build_analysis_llm(
    settings: Settings | None = None, *, timeout_seconds: float | None = None
) -> LLMClient | None:
    """Build the configured analysis LLM: local Ollama first, remote gateway/provider second.

    Returns None when neither a local model nor the complete remote triple
    (API key, model, base URL) is configured. When a fallback model is also
    configured, wraps both in a FallbackLLMClient gateway adapter.
    """

    settings = settings or get_settings()
    if settings.local_llm_model:
        return OllamaLLMClient(settings)
    if all(getattr(settings, field) for field in REMOTE_REQUIRED_FIELDS):
        primary_client = ProviderLLMClient(
            settings,
            timeout_seconds=timeout_seconds or settings.llm_primary_timeout_seconds,
        )
        if all(getattr(settings, field) for field in FALLBACK_REQUIRED_FIELDS):
            fallback_settings = _build_fallback_settings(settings)
            fallback_client = ProviderLLMClient(
                fallback_settings,
                timeout_seconds=timeout_seconds or settings.llm_timeout_seconds,
            )
            return FallbackLLMClient(
                primary_client=primary_client,
                fallback_client=fallback_client,
                primary_timeout_seconds=settings.llm_primary_timeout_seconds,
                circuit_breaker_threshold=settings.llm_circuit_breaker_threshold,
                circuit_breaker_cooldown_seconds=settings.llm_circuit_breaker_cooldown_seconds,
            )
        return primary_client
    return None


def describe_fallback_setup(settings: Settings | None = None) -> dict:
    """Describe the fallback analysis-LLM gateway configuration."""
    settings = settings or get_settings()
    fallback_configured = all(
        getattr(settings, field) for field in FALLBACK_REQUIRED_FIELDS
    )
    return {
        "configured": fallback_configured,
        "provider": settings.llm_fallback_provider or "",
        "model": settings.llm_fallback_model or "",
        "base_url": settings.llm_fallback_base_url or "",
        "has_api_key": bool(settings.llm_fallback_api_key),
        "primary_timeout_seconds": settings.llm_primary_timeout_seconds,
    }


def describe_llm_setup(settings: Settings | None = None) -> dict:
    """Describe the active analysis-LLM configuration for UI guidance.

    Returns ``{"configured", "mode", "model", "missing", "provider", "base_url"}``
    where ``mode`` is "local", "remote" or None.
    """

    settings = settings or get_settings()
    if settings.local_llm_model:
        return {
            "configured": True,
            "mode": "local",
            "model": settings.local_llm_model,
            "provider": "ollama",
            "base_url": settings.local_llm_base_url,
            "missing": [],
        }
    missing = [
        env_name
        for field, env_name in REMOTE_REQUIRED_FIELDS.items()
        if not getattr(settings, field)
    ]
    configured = len(missing) == 0
    return {
        "configured": configured,
        "mode": "remote" if configured else None,
        "model": settings.llm_model if configured else None,
        "provider": settings.llm_provider or "",
        "base_url": settings.llm_base_url or "",
        "missing": missing,
    }


