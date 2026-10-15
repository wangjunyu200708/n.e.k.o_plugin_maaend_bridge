"""Prompt-table invariants.

A missing translation is invisible at runtime — ``t()`` falls back and the
character simply speaks the wrong language mid-conversation. A translation
that drops a ``{placeholder}`` is worse: ``str.format`` raises, the cue is
silently skipped, and the user never learns the task finished.
"""
from __future__ import annotations

import re

import pytest
from maaend_bridge_plugin import prompts

# ``{MASTER_NAME}`` / ``{LANLAN_NAME}`` live in the source as doubled braces
# (they are substituted downstream by main_server, not by ``str.format``), so
# they are excluded from the placeholder comparison.
_PLACEHOLDER_RE = re.compile(r"(?<!\{)\{([a-zA-Z_][a-zA-Z0-9_]*)\}(?!\})")
_DOWNSTREAM = {"MASTER_NAME", "LANLAN_NAME"}


def _placeholders(text: str) -> set:
    return {name for name in _PLACEHOLDER_RE.findall(text) if name not in _DOWNSTREAM}


def test_every_key_has_every_locale():
    for key, bundle in prompts.PROMPTS.items():
        missing = [lang for lang in prompts.SUPPORTED_LANGS if lang not in bundle]
        assert not missing, f"{key} is missing {missing}"


def test_no_locale_has_an_empty_string():
    for key, bundle in prompts.PROMPTS.items():
        for lang, text in bundle.items():
            assert text.strip(), f"{key}[{lang}] is empty"


def test_no_locale_carries_an_untranslated_extra_key():
    for key, bundle in prompts.PROMPTS.items():
        extra = [lang for lang in bundle if lang not in prompts.SUPPORTED_LANGS]
        assert not extra, f"{key} has unexpected locales {extra}"


def test_placeholders_match_across_locales():
    """A translation that drops ``{label}`` would raise inside ``str.format``."""
    for key, bundle in prompts.PROMPTS.items():
        expected = _placeholders(bundle["en"])
        for lang, text in bundle.items():
            assert _placeholders(text) == expected, (
                f"{key}[{lang}] placeholders {sorted(_placeholders(text))} "
                f"do not match en {sorted(expected)}"
            )


def test_unknown_key_raises_rather_than_rendering_nothing():
    with pytest.raises(KeyError):
        prompts.t("NO_SUCH_KEY_ANYWHERE")


def test_missing_locale_falls_back_to_english():
    assert prompts.t("STATUS_READY", lang="xx") == prompts.PROMPTS["STATUS_READY"]["en"]


def test_a_missing_chinese_variant_falls_back_to_simplified(monkeypatch):
    monkeypatch.delitem(prompts.PROMPTS["STATUS_READY"], "zh-TW", raising=False)
    assert prompts.t("STATUS_READY", lang="zh-TW") == prompts.PROMPTS["STATUS_READY"]["zh"]


def test_master_name_placeholder_survives_with_and_without_formatting(monkeypatch):
    """Doubled braces must collapse to single ones on both paths."""
    key = "ERR_UNREACHABLE"
    monkeypatch.setitem(prompts.PROMPTS[key], "en", "ask {{MASTER_NAME}} to start it")
    assert prompts.t(key, lang="en") == "ask {MASTER_NAME} to start it"
    assert prompts.t(key, lang="en", unused="") == "ask {MASTER_NAME} to start it"


def test_master_name_placeholder_in_a_formatted_template(monkeypatch):
    key = "CUE_DONE_OK"
    monkeypatch.setitem(
        prompts.PROMPTS[key], "en", "{{MASTER_NAME}}: {label} finished in {elapsed}"
    )
    assert prompts.t(key, lang="en", label="X", elapsed="1s") == "{MASTER_NAME}: X finished in 1s"


def test_lanlan_name_placeholder_is_also_collapsed(monkeypatch):
    key = "CUE_PROGRESS"
    monkeypatch.setitem(prompts.PROMPTS[key], "en", "{{LANLAN_NAME}} is at {nodes}")
    assert prompts.t(key, lang="en", nodes="A") == "{LANLAN_NAME} is at A"


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [(0, "0s"), (5, "5s"), (59, "59s"), (60, "1m00s"), (67.2, "1m07s"), (3600, "1h00m"), (3725, "1h02m")],
)
def test_format_elapsed(seconds, expected):
    assert prompts.format_elapsed(seconds) == expected


def test_format_elapsed_survives_junk():
    for junk in (None, "", "abc", {}, []):
        assert prompts.format_elapsed(junk) == "-"


def test_format_catalogue_drops_the_code_when_it_equals_the_label():
    text = prompts.format_catalogue(
        [
            {"name": "DailyRewards", "label": "🎁每日奖励"},
            {"name": "SameName", "label": "SameName"},
        ]
    )
    assert "🎁每日奖励 (DailyRewards)" in text
    assert text.count("SameName") == 1


def test_format_catalogue_survives_junk():
    for junk in (None, {}, "nope", []):
        assert prompts.format_catalogue(junk) == ""


def test_status_busy_uses_the_task_label():
    assert "每日奖励" in prompts.t("STATUS_BUSY", lang="zh", task="每日奖励")


def test_error_lines_never_name_a_host_or_port():
    """Transport details must never reach the transcript.

    ``ERR_FORBIDDEN`` is the one deliberate exception: the fix for it *is* a
    loopback address, and the panel's Host/Origin allow-list means any other
    value will keep failing. That is a configuration instruction, not a
    leaked connection detail — no port and no endpoint path appears in it.
    """
    for key in ("ERR_UNREACHABLE", "ERR_AUTH", "ERR_NOT_READY", "ERR_GENERIC"):
        for lang in prompts.SUPPORTED_LANGS:
            text = prompts.t(key, lang=lang)
            assert "127.0.0.1" not in text
            assert "12702" not in text
            assert "/api/" not in text


def test_the_forbidden_line_teaches_the_fix_without_leaking_a_port():
    for lang in prompts.SUPPORTED_LANGS:
        text = prompts.t("ERR_FORBIDDEN", lang=lang)
        assert "127.0.0.1" in text
        assert "12702" not in text
        assert "/api/" not in text
