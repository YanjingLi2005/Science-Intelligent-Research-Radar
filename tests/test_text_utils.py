from radar.config import Settings
from radar.llm.text_utils import (
    prompt_char_budget,
    truncate_for_prompt,
    _cjk_ratio,
)


def test_short_text_is_returned_unchanged():
    text = "short manuscript text"
    assert truncate_for_prompt(text, max_chars=1_000) == text


def test_long_text_keeps_head_and_tail_with_omission_marker():
    head = "INTRO " + "a" * 500
    middle = "m" * 5_000
    tail = "CONCLUSION " + "z" * 500
    text = head + middle + tail

    truncated = truncate_for_prompt(text, max_chars=2_000)

    assert len(truncated) <= 2_000
    assert truncated.startswith("INTRO ")
    assert truncated.endswith("z" * 500)
    assert "[... omitted " in truncated
    assert " chars ...]" in truncated
    assert middle not in truncated


def test_explicit_budget_overrides_settings_default():
    text = "x" * 10_000
    truncated = truncate_for_prompt(text, max_chars=500)
    assert len(truncated) <= 500


def test_default_budget_derives_from_settings_token_limit():
    # Output cap no longer drives the input budget; the context window does.
    settings = Settings(_env_file=None, llm_max_tokens=4_096, llm_context_tokens=8_000)
    budget = prompt_char_budget(settings)
    assert budget == int(8_000 * 3.5) - 6_000

    text = "y" * (budget + 5_000)
    truncated = truncate_for_prompt(text, max_chars=budget, purpose="test")
    assert len(truncated) <= budget


def test_cjk_text_gets_shrunk_cap_under_default_budget():
    """M23 regression: a CJK-heavy block packs ~2x the tokens per character;
    the default char budget must shrink so it still fits the context window."""
    settings = Settings(_env_file=None, llm_context_tokens=8_000)
    budget = prompt_char_budget(settings)
    ascii_text = "y" * (budget + 5_000)
    cjk_text = "研" * (budget + 5_000)  # pure CJK

    ascii_truncated = truncate_for_prompt(ascii_text, max_chars=budget, purpose="test")
    cjk_truncated = truncate_for_prompt(cjk_text, max_chars=budget, purpose="test")

    # English keeps roughly the full budget; CJK shrinks to the script-aware cap.
    assert len(ascii_truncated) <= budget
    assert len(ascii_truncated) > int(budget * 0.8)
    assert len(cjk_truncated) <= int(budget * 1.8 / 3.5) + 1_000
    assert len(cjk_truncated) < len(ascii_truncated)


def test_cjk_ratio_detects_mixed_scripts():
    assert _cjk_ratio("纯中文文本") == 1.0
    assert _cjk_ratio("pure ascii") == 0.0
    assert 0.0 < _cjk_ratio("mixed 中文 text") < 1.0


def test_default_budget_uses_mode_defaults():
    remote = Settings(_env_file=None)
    assert prompt_char_budget(remote) == int(60_000 * 3.5) - 6_000
    local = Settings(_env_file=None, local_llm_model="qwen3:4b")
    assert prompt_char_budget(local) == int(28_000 * 3.5) - 6_000
