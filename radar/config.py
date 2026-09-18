"""Typed application configuration loaded from environment variables."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


# Config layering: the project .env ships with the repo (advanced users), while
# data/settings.local.env is written by the in-app settings page and wins.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ENV_FILE = PROJECT_ROOT / ".env"
LOCAL_SETTINGS_FILE = PROJECT_ROOT / "data" / "settings.local.env"

# Real API keys are long (sk-… / gsk_…); placeholder values like "22" or
# "your-key-here" must be treated as unset, or the app would happily make
# live calls with a junk key (and 401) instead of reporting "not configured".
MIN_API_KEY_LENGTH = 16


def _clean_api_key(value: str | None) -> str | None:
    if not value:
        return None
    stripped = value.strip()
    if len(stripped) < MIN_API_KEY_LENGTH:
        return None
    return stripped


# USD per one million tokens: (input, output).
MODEL_TOKEN_PRICES_USD: dict[str, tuple[float, float]] = {
    # Current DeepSeek V4 cache-miss input and output rates. Keep legacy
    # aliases so historical runs can still be estimated.
    "deepseek-v4-flash": (0.14, 0.28),
    "deepseek-v4-pro": (0.435, 0.87),
    "deepseek-chat": (0.27, 1.10),
    "deepseek-reasoner": (0.55, 2.19),
}


def estimate_llm_cost_usd(
    model: str | None, input_tokens: int, output_tokens: int
) -> float:
    """Estimate the USD cost of a model run from token usage.

    Unknown models return 0.0; their token counts are still recorded so the
    ledger keeps usage even without a known price.
    """

    prices = MODEL_TOKEN_PRICES_USD.get((model or "").strip().lower())
    if prices is None:
        return 0.0
    input_price, output_price = prices
    return round(
        (input_tokens * input_price + output_tokens * output_price) / 1_000_000, 6
    )


class Settings(BaseSettings):
    """Runtime settings for the local Research Radar application."""

    model_config = SettingsConfigDict(
        # Later files override earlier ones, so the UI-written local file wins.
        env_file=(DEFAULT_ENV_FILE, LOCAL_SETTINGS_FILE),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    @field_validator(
        "llm_api_key", "llm_fallback_api_key", "embedding_api_key", mode="before"
    )
    @classmethod
    def _drop_placeholder_keys(cls, value):
        return _clean_api_key(value)

    app_env: Literal["development", "test", "production"] = "development"
    database_url: str = "sqlite:///data/research_radar.db"

    # Public deployments can disable self-service registration and create
    # accounts through the admin users API instead.
    allow_registration: bool = True

    llm_provider: str | None = None
    llm_api_key: str | None = Field(default=None, repr=False)
    llm_model: str | None = None
    llm_base_url: str | None = None
    llm_thinking: Literal["enabled", "disabled"] = "enabled"
    # OpenAI supports a wider effort range; DeepSeek maps low/medium to high
    # and xhigh to max. Provider-specific request builders handle the details.
    llm_reasoning_effort: Literal[
        "none", "minimal", "low", "medium", "high", "xhigh", "max"
    ] = "high"
    llm_max_tokens: int = Field(default=4096, ge=256, le=384_000)
    # Input-side context window used for prompt truncation budgets. Distinct
    # from llm_max_tokens (output cap). None derives a safe default per mode.
    llm_context_tokens: int | None = Field(default=None, ge=4_000, le=1_000_000)
    llm_timeout_seconds: float = Field(default=120.0, ge=15.0, le=900.0)
    llm_max_retries: int = Field(default=2, ge=0, le=5)
    llm_retry_backoff_seconds: float = Field(default=2.0, ge=0.0, le=60.0)
    llm_manuscript_timeout_seconds: float = Field(default=240.0, ge=45.0, le=900.0)

    # Multi-model gateway: Fallback model configuration on timeout (e.g. >8s) or provider failure
    llm_fallback_provider: str | None = None
    llm_fallback_api_key: str | None = Field(default=None, repr=False)
    llm_fallback_model: str | None = None
    llm_fallback_base_url: str | None = None
    llm_primary_timeout_seconds: float = Field(default=8.0, ge=1.0, le=120.0)
    llm_circuit_breaker_threshold: int = Field(default=3, ge=1, le=10)
    llm_circuit_breaker_cooldown_seconds: float = Field(default=60.0, ge=5.0, le=600.0)

    # Private local reasoning model used by the live radar before any remote LLM.
    local_llm_model: str | None = None
    local_llm_base_url: str = "http://127.0.0.1:11434"
    local_llm_timeout_seconds: float = Field(default=300.0, ge=30.0, le=1800.0)

    embedding_provider: str | None = None
    embedding_api_key: str | None = Field(default=None, repr=False)
    embedding_model: str | None = None
    embedding_base_url: str | None = None
    embedding_timeout_seconds: float = Field(default=60.0, ge=5.0, le=600.0)
    embedding_max_retries: int = Field(default=3, ge=0, le=5)

    data_dir: Path = Path("data")
    fixture_case_dir: Path = Path("tests/fixtures/golden_case")

    # PDF parsing backend: pymupdf (default, lightweight) or docling
    # (optional extra, better for table/formula-heavy manuscripts).
    pdf_parser_backend: Literal["pymupdf", "docling"] = "pymupdf"

    # Persistent vector store for semantic paper retrieval.
    vector_store_dir: str = "data/vector_store"
    vector_store_enabled: bool = True

    # Optional local NLI second opinion. Disabled by default so the base
    # deployment does not require transformers/PyTorch.
    nli_enabled: bool = False

    # PaperQA2 integration for contradiction detection.
    paperqa2_enabled: bool = False
    paperqa2_model: str = "gpt-4.1-mini"
    paperqa2_embedding_model: str = "text-embedding-3-small"

    # Contact address sent with Crossref API requests (politeness pool).
    crossref_mailto: str = "local@example.invalid"

    # Optional Semantic Scholar key. Anonymous access works at lower limits.
    semantic_scholar_api_key: str | None = Field(default=None, repr=False)


def get_settings() -> Settings:
    """Return settings for the active tenant, or the global defaults.

    Inside a request the per-user settings file (their own LLM key etc.) is
    layered over ``.env``; outside a request the legacy single-user defaults
    apply so existing tooling and tests keep working unchanged.
    """
    from radar.tenant import current_tenant

    tenant = current_tenant()
    if tenant is None:
        return _global_settings()
    return _tenant_settings(tenant.settings_file)


@lru_cache(maxsize=1)
def _global_settings() -> Settings:
    return Settings()


@lru_cache(maxsize=128)
def _tenant_settings(settings_path: Path) -> Settings:
    # A tenant reads ONLY its own settings file (plus OS env). Layering the
    # shared project .env here would leak one account's LLM_API_KEY into every
    # other account — the exact cross-tenant leak multi-user mode must prevent.
    return Settings(_env_file=settings_path)


def _format_env_value(value: str) -> str:
    """Quote a dotenv value only when it could break simple KEY=VALUE parsing."""

    if value and value.strip() == value and "#" not in value and '"' not in value:
        return value
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def save_local_settings(
    updates: dict[str, str], path: Path | None = None
) -> Path:
    """Write UI-edited settings into the local override env file.

    Only ``data/settings.local.env`` is touched (or the active tenant's
    settings file when running in a request, or ``path`` in tests); the
    project ``.env`` is never modified. Lines for keys not present in
    ``updates`` — including comments — are preserved, an empty value writes
    ``KEY=`` which the settings layer treats as unset. The cached settings
    objects are cleared so the next ``get_settings()`` call picks the changes
    up.
    """

    target = path
    if target is None:
        from radar.tenant import current_tenant

        tenant = current_tenant()
        target = tenant.settings_file if tenant is not None else LOCAL_SETTINGS_FILE
    lines = (
        target.read_text(encoding="utf-8").splitlines() if target.exists() else []
    )
    remaining = {key.strip().upper(): value for key, value in updates.items()}
    output: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.split("=", 1)[0].strip().upper()
            if key in remaining:
                output.append(f"{key}={_format_env_value(remaining.pop(key))}")
                continue
        output.append(line)
    output.extend(
        f"{key}={_format_env_value(value)}" for key, value in remaining.items()
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(output) + "\n", encoding="utf-8")
    _global_settings.cache_clear()
    _tenant_settings.cache_clear()
    return target


def mask_secret(value: str | None, *, visible_tail: int = 4) -> str:
    """Mask an API key for display, keeping a recognizable prefix and the tail."""

    if not value:
        return ""
    if len(value) <= visible_tail:
        return "•" * 4
    prefix = "sk-" if value.startswith("sk-") else ""
    return f"{prefix}{'•' * 4}{value[-visible_tail:]}"
