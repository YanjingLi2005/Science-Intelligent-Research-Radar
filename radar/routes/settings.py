"""Settings and model configuration API routes."""

from __future__ import annotations

from typing import Any

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import ValidationError

from radar.api_schemas import SettingsUpdate
from radar.config import (
    Settings,
    get_settings as _default_get_settings,
    save_local_settings as _default_save_local_settings,
)
from radar.llm.factory import describe_fallback_setup, describe_llm_setup

router = APIRouter(tags=["settings"])

_ALLOWED_SETTINGS_KEYS = frozenset(
    {
        "LLM_PROVIDER",
        "LLM_API_KEY",
        "LLM_MODEL",
        "LLM_BASE_URL",
        "LLM_THINKING",
        "LLM_REASONING_EFFORT",
        "LLM_MAX_TOKENS",
        "LLM_CONTEXT_TOKENS",
        "LLM_TIMEOUT_SECONDS",
        "LLM_FALLBACK_PROVIDER",
        "LLM_FALLBACK_API_KEY",
        "LLM_FALLBACK_MODEL",
        "LLM_FALLBACK_BASE_URL",
        "LLM_PRIMARY_TIMEOUT_SECONDS",
        "LLM_CIRCUIT_BREAKER_THRESHOLD",
        "LLM_CIRCUIT_BREAKER_COOLDOWN_SECONDS",
        "LOCAL_LLM_MODEL",
        "LOCAL_LLM_BASE_URL",
        "LOCAL_LLM_TIMEOUT_SECONDS",
        "EMBEDDING_PROVIDER",
        "EMBEDDING_API_KEY",
        "EMBEDDING_MODEL",
        "EMBEDDING_BASE_URL",
        "EMBEDDING_TIMEOUT_SECONDS",
        "EMBEDDING_MAX_RETRIES",
        "PDF_PARSER_BACKEND",
        "NLI_ENABLED",
        "PAPERQA2_ENABLED",
        "CROSSREF_MAILTO",
        "SEMANTIC_SCHOLAR_API_KEY",
    }
)

_MODEL_CATALOG = {
    "deepseek": [
        {
            "id": "deepseek-v4-flash",
            "label": "V4 Flash · 推荐，速度与成本优先",
        },
        {
            "id": "deepseek-v4-pro",
            "label": "V4 Pro · 复杂研究判断优先",
        },
    ],
    "openai": [
        {
            "id": "gpt-5.6-terra",
            "label": "GPT-5.6 Terra · 推荐，质量/成本平衡",
        },
        {
            "id": "gpt-5.6-sol",
            "label": "GPT-5.6 Sol · 最强复杂分析",
        },
        {
            "id": "gpt-5.6-luna",
            "label": "GPT-5.6 Luna · 高频轻量任务",
        },
    ],
}


def _get_current_settings():
    try:
        import radar.api as api
        return getattr(api, "get_settings", _default_get_settings)()
    except Exception:
        return _default_get_settings()


def _save_settings(*args, **kwargs):
    try:
        import radar.api as api
        fn = getattr(api, "save_local_settings", _default_save_local_settings)
        return fn(*args, **kwargs)
    except Exception:
        return _default_save_local_settings(*args, **kwargs)


@router.get("/api/settings")
def get_app_settings() -> dict[str, Any]:
    """Return the current LLM and embedding configuration."""
    settings = _get_current_settings()
    llm = describe_llm_setup(settings)
    embedding_provider = (settings.embedding_provider or "").strip().lower()
    embedding_common = bool(settings.embedding_model and settings.embedding_base_url)
    emb_configured = embedding_common and (
        embedding_provider == "ollama"
        or (
            embedding_provider in {"openai", "openai_compatible"}
            and bool(settings.embedding_api_key)
        )
    )
    return {
        "llm": {
            "configured": llm["configured"],
            "mode": llm["mode"],
            "model": llm["model"],
            "missing": llm["missing"],
            "provider": llm["provider"],
            "base_url": llm["base_url"],
            "has_api_key": bool(settings.llm_api_key),
            "thinking": settings.llm_thinking,
            "reasoning_effort": settings.llm_reasoning_effort,
        },
        "fallback_llm": describe_fallback_setup(settings),
        "embedding": {
            "configured": emb_configured,
            "model": settings.embedding_model or "",
            "provider": settings.embedding_provider or "",
            "base_url": settings.embedding_base_url or "",
            "has_api_key": bool(settings.embedding_api_key),
        },
        "local_llm": {
            "model": settings.local_llm_model or "",
            "base_url": settings.local_llm_base_url,
        },
        "optional_checks": {
            "nli_enabled": settings.nli_enabled,
            "paperqa2_enabled": settings.paperqa2_enabled,
        },
        "model_catalog": _MODEL_CATALOG,
        "pdf_parser_backend": settings.pdf_parser_backend,
    }


@router.put("/api/settings")
def update_app_settings(body: SettingsUpdate) -> dict[str, Any]:
    """Write one or more settings keys to the local override env file."""
    normalized = {key.strip().upper(): value for key, value in body.updates.items()}
    unknown = sorted(set(normalized) - _ALLOWED_SETTINGS_KEYS)
    if unknown:
        raise HTTPException(400, f"unsupported settings keys: {', '.join(unknown)}")
    # Older frontend builds used this sentinel. Never persist it as a real key.
    normalized = {key: value for key, value in normalized.items() if value != "__keep__"}
    try:
        probe_values = _get_current_settings().model_dump()
        probe_values.update({key.lower(): value for key, value in normalized.items()})
        Settings(**probe_values)
    except (ValidationError, ValueError) as exc:
        raise HTTPException(400, f"invalid settings value: {exc}")
    try:
        _save_settings(normalized)
    except Exception as exc:
        raise HTTPException(500, str(exc))
    return {"saved": list(normalized.keys())}


@router.post("/api/settings/test")
def test_app_settings() -> dict[str, Any]:
    """Verify the active model endpoint and confirm the selected model exists."""
    settings = _get_current_settings()
    setup = describe_llm_setup(settings)
    if not setup["configured"]:
        raise HTTPException(400, f"LLM 未完整配置：{', '.join(setup['missing'])}")

    try:
        if setup["mode"] == "local":
            response = httpx.get(
                f"{settings.local_llm_base_url.rstrip('/')}/api/tags", timeout=10
            )
            response.raise_for_status()
            available = [
                item.get("name", "")
                for item in (response.json().get("models") or [])
                if item.get("name")
            ]
        else:
            response = httpx.get(
                f"{settings.llm_base_url.rstrip('/')}/models",
                headers={"Authorization": f"Bearer {settings.llm_api_key}"},
                timeout=15,
            )
            response.raise_for_status()
            available = [
                item.get("id", "")
                for item in (response.json().get("data") or [])
                if item.get("id")
            ]
    except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
        raise HTTPException(502, f"模型服务连接失败：{exc}")

    selected = str(setup["model"] or "")
    if available and selected not in available:
        raise HTTPException(400, f"服务已连接，但找不到模型：{selected}")
    return {
        "ok": True,
        "mode": setup["mode"],
        "provider": setup["provider"],
        "model": selected,
        "available_models": available,
    }
