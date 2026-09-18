"""The default installation must not require the local PyTorch NLI stack."""

from radar.config import Settings
from radar.services import weekly_radar_service as weekly_module


def test_nli_is_disabled_by_default():
    assert Settings(_env_file=None).nli_enabled is False


def test_default_nli_builder_skips_optional_stack(monkeypatch):
    monkeypatch.setattr(
        weekly_module, "get_settings", lambda: Settings(_env_file=None)
    )

    assert weekly_module._default_nli_builder() is None


def test_paperqa2_builder_requires_explicit_enable():
    assert weekly_module._default_paperqa2_builder(Settings(_env_file=None)) is None
