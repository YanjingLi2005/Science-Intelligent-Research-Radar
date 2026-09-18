"""Local settings persistence: UI-written env file layering and masking."""

from radar.config import Settings, get_settings, mask_secret, save_local_settings


def test_save_writes_and_preserves_unrelated_lines(tmp_path):
    target = tmp_path / "settings.local.env"
    target.write_text("# 注释\nLLM_MODEL=old-model\nCROSSREF_MAILTO=a@b.c\n")
    save_local_settings(
        {"LLM_MODEL": "new-model", "LLM_API_KEY": "sk-secret1234"}, path=target
    )
    content = target.read_text(encoding="utf-8")
    assert "# 注释" in content
    assert "CROSSREF_MAILTO=a@b.c" in content
    assert "LLM_MODEL=new-model" in content
    assert "old-model" not in content
    assert "LLM_API_KEY=sk-secret1234" in content


def test_local_file_overrides_project_env(tmp_path):
    base = tmp_path / ".env"
    local = tmp_path / "settings.local.env"
    base.write_text("LLM_MODEL=base-model\nLLM_BASE_URL=https://api.base.example\n")
    save_local_settings({"LLM_MODEL": "local-model"}, path=local)
    settings = Settings(_env_file=(str(base), str(local)))
    assert settings.llm_model == "local-model"
    # Keys absent from the local file still come from the project .env.
    assert settings.llm_base_url == "https://api.base.example"


def test_empty_value_clears_key(tmp_path):
    target = tmp_path / "settings.local.env"
    save_local_settings({"LOCAL_LLM_MODEL": "qwen3:4b"}, path=target)
    save_local_settings({"LOCAL_LLM_MODEL": ""}, path=target)
    settings = Settings(_env_file=str(target))
    assert not settings.local_llm_model


def test_save_never_touches_project_env(tmp_path):
    base = tmp_path / ".env"
    base.write_text("LLM_MODEL=base-model\n")
    before = base.read_text(encoding="utf-8")
    save_local_settings(
        {"LLM_MODEL": "local-model"}, path=tmp_path / "settings.local.env"
    )
    assert base.read_text(encoding="utf-8") == before


def test_save_clears_cached_settings(tmp_path):
    from radar.config import _global_settings, _tenant_settings

    _global_settings.cache_clear()
    _tenant_settings.cache_clear()
    cached = get_settings()
    save_local_settings({"LLM_MODEL": "whatever"}, path=tmp_path / "settings.local.env")
    try:
        assert get_settings() is not cached
    finally:
        _global_settings.cache_clear()
        _tenant_settings.cache_clear()


def test_value_with_special_chars_is_quoted(tmp_path):
    target = tmp_path / "settings.local.env"
    save_local_settings({"LLM_PROVIDER": "my provider #1"}, path=target)
    settings = Settings(_env_file=str(target))
    assert settings.llm_provider == "my provider #1"


def test_invalid_value_is_rejected_before_persisting(tmp_path, monkeypatch):
    """M34 regression: a bad value (LLM_MAX_TOKENS: abc) must be rejected with
    400 before it can poison get_settings() and 500 every endpoint."""
    import radar.api as api_module
    from starlette.testclient import TestClient

    from radar.config import get_settings
    from radar.db import create_db_engine, init_database
    from sqlalchemy.orm import sessionmaker

    engine = create_db_engine(f"sqlite:///{tmp_path / 'settings.db'}")
    init_database(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(api_module, "SessionLocal", factory)
    import radar.db as db_module
    monkeypatch.setattr(db_module, "SessionLocal", factory)
    monkeypatch.setattr(db_module, "engine", engine)

    from radar.config import _global_settings, _tenant_settings

    _global_settings.cache_clear()
    _tenant_settings.cache_clear()
    try:
        with TestClient(api_module.app) as client:
            bad = client.put(
                "/api/settings",
                json={"updates": {"LLM_MAX_TOKENS": "abc"}},
            )
            assert bad.status_code == 400
            assert "invalid settings value" in bad.json()["detail"]

            # The app still works and the bad value was never persisted.
            ok = client.put(
                "/api/settings",
                json={"updates": {"LLM_MODEL": "deepseek-v4-flash"}},
            )
            assert ok.status_code == 200
    finally:
        _global_settings.cache_clear()
        _tenant_settings.cache_clear()
        engine.dispose()



def test_placeholder_api_keys_are_treated_as_unset():
    """Short/placeholder keys ('22') must be treated as unset — otherwise the
    app makes live calls with a junk key and 401s instead of showing
    'LLM not configured'."""
    placeholder = Settings(_env_file=None, llm_api_key="22", embedding_api_key="22")
    assert placeholder.llm_api_key is None
    assert placeholder.embedding_api_key is None

    real = Settings(
        _env_file=None,
        llm_api_key="sk-" + "a" * 40,
        embedding_api_key="gsk_" + "b" * 40,
    )
    assert real.llm_api_key is not None
    assert real.embedding_api_key is not None


def test_mask_secret():
    assert mask_secret(None) == ""
    assert mask_secret("") == ""
    assert mask_secret("sk-abcdef123456") == "sk-••••3456"
    assert mask_secret("abcdef123456") == "••••3456"
    assert mask_secret("abc") == "••••"
    assert "abcdef" not in mask_secret("sk-abcdef123456")
