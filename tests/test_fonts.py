"""Tests for pebble_mcp.fonts."""

from __future__ import annotations

import re

import pytest

from pebble_mcp.fonts import (
    SYSTEM_FONTS,
    font_plan,
    get_font,
    minimal_character_regex,
)

# ---------------------------------------------------------------------------
# Font table sanity
# ---------------------------------------------------------------------------


def test_all_font_keys_start_with_font_key_prefix():
    for f in SYSTEM_FONTS:
        assert f.key.startswith("FONT_KEY_"), f.key


def test_no_duplicate_keys():
    keys = [f.key for f in SYSTEM_FONTS]
    assert len(keys) == len(set(keys))


def test_leco_fonts_are_numbers_only():
    leco = [f for f in SYSTEM_FONTS if f.family == "leco"]
    assert leco, "expected LECO fonts in the table"
    for f in leco:
        assert f.numbers_only is True, f.key
        assert f.glyphs is not None
        # Digits must always be covered; letters only allowed for AM/PM.
        assert set("0123456789") <= f.glyphs


def test_leco_am_pm_variants_include_meridiem_letters():
    am_pm = get_font("FONT_KEY_LECO_26_BOLD_NUMBERS_AM_PM")
    assert am_pm is not None
    assert {"A", "M", "P"} <= am_pm.glyphs

    plain = get_font("FONT_KEY_LECO_42_NUMBERS")
    assert plain is not None
    assert "A" not in plain.glyphs


def test_gothic_fonts_are_full_text_not_numbers_only():
    gothic = [f for f in SYSTEM_FONTS if f.family == "gothic"]
    assert gothic
    for f in gothic:
        assert f.numbers_only is False, f.key
        assert f.glyphs is None


def test_gothic_sizes_match_design_md_scale():
    # DESIGN.md: "Titles/labels: GOTHIC_28_BOLD down to GOTHIC_14".
    sizes = {f.size for f in SYSTEM_FONTS if f.family == "gothic"}
    assert sizes == {14, 18, 24, 28}
    # No stale "Gothic 09" (see fonts.py module docstring re: ECOSYSTEM.md).
    assert get_font("FONT_KEY_GOTHIC_09") is None


def test_hero_leco_keys_present_per_design_md():
    # DESIGN.md: "Numbers: LECO_36_BOLD_NUMBERS / LECO_42_NUMBERS for the hero value."
    assert get_font("FONT_KEY_LECO_36_BOLD_NUMBERS") is not None
    assert get_font("FONT_KEY_LECO_42_NUMBERS") is not None


def test_leco_60_variants_are_emery_only():
    for key in ("FONT_KEY_LECO_60_NUMBERS_AM_PM", "FONT_KEY_LECO_60_BOLD_NUMBERS_AM_PM"):
        f = get_font(key)
        assert f is not None
        assert f.min_platform == "emery"
    # Plenty of other LECO fonts are NOT platform-restricted.
    assert get_font("FONT_KEY_LECO_42_NUMBERS").min_platform is None


# ---------------------------------------------------------------------------
# minimal_character_regex golden cases
# ---------------------------------------------------------------------------


def test_minimal_character_regex_clock_digits_no_collapse():
    # "15:37" -> unique sorted chars '1','3','5','7',':' -- none adjacent in
    # codepoint order, so no ranges collapse.
    assert minimal_character_regex("15:37") == "[1357:]"


def test_minimal_character_regex_matches_only_input_chars():
    pattern = minimal_character_regex("15:37")
    compiled = re.compile(pattern)
    for ch in "15:37":
        assert compiled.fullmatch(ch), (pattern, ch)
    for ch in "0246890 ABC":
        assert not compiled.fullmatch(ch), (pattern, ch)


def test_minimal_character_regex_collapses_contiguous_digit_run():
    # All ten digits are one contiguous codepoint run -> collapses to 0-9.
    assert minimal_character_regex("9876543210") == "[0-9]"


def test_minimal_character_regex_two_char_run_not_collapsed():
    # Only a 2-run ("12"); collapsing buys nothing over listing both chars.
    assert minimal_character_regex("21") == "[12]"


def test_minimal_character_regex_three_char_run_collapses():
    assert minimal_character_regex("cba") == "[a-c]"


@pytest.mark.parametrize(
    "text,forbidden_raw",
    [
        ("a]b", "]"),
        ("a-b", "-"),
        ("a^b", "^"),
        ("a\\b", "\\"),
    ],
)
def test_minimal_character_regex_escapes_class_specials(text, forbidden_raw):
    pattern = minimal_character_regex(text)
    # Must compile as a valid regex (would raise re.error if unescaped and
    # e.g. '^' landed first or '-' formed a bogus range).
    compiled = re.compile(pattern)
    for ch in set(text):
        assert compiled.fullmatch(ch)


def test_minimal_character_regex_rejects_empty_text():
    # Message must point at the public tool (font_plan) and its actual
    # argument name, not the internal minimal_character_regex() function.
    with pytest.raises(ValueError, match="font_plan"):
        minimal_character_regex("")


# ---------------------------------------------------------------------------
# ADVERSARIAL (roadmap 2.3): the output must be a VALID regex for EVERY input,
# with no re warnings, and match exactly the input glyph set.
# ---------------------------------------------------------------------------


def test_minimal_character_regex_open_bracket_no_futurewarning():
    # A literal '[' inside the class produced Python's "Possible nested set"
    # FutureWarning (and is a future error). It must be escaped.
    import warnings

    with warnings.catch_warnings():
        warnings.simplefilter("error")  # turn any regex FutureWarning into a failure
        pattern = minimal_character_regex("a[b")
        compiled = re.compile(pattern)
    for ch in "a[b":
        assert compiled.fullmatch(ch)
    assert not compiled.fullmatch("c")


@pytest.mark.parametrize("ch", ["[", "]", "^", "-", "\\"])
def test_minimal_character_regex_single_special_char(ch):
    import warnings

    with warnings.catch_warnings():
        warnings.simplefilter("error")
        compiled = re.compile(minimal_character_regex(ch))
    assert compiled.fullmatch(ch)


def test_minimal_character_regex_fuzz_always_valid_and_exact():
    import random
    import warnings

    random.seed(1234)
    alphabet = [chr(i) for i in range(0, 0x300)] + [chr(i) for i in range(0x1F600, 0x1F620)]
    for _ in range(500):
        s = "".join(random.choice(alphabet) for _ in range(random.randint(1, 40)))
        pattern = minimal_character_regex(s)
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            compiled = re.compile(pattern)  # must never raise re.error or warn
        for actual in set(s):
            assert compiled.fullmatch(actual), (s, pattern, actual)


def test_font_plan_control_chars_and_emoji_do_not_crash():
    for text in ["\x00\x01\x02", "😀😀", "abcمرحبا", "x" * 5000]:
        result = font_plan(text)
        assert result["mode"] == "text"
        re.compile(result["minimal_character_regex"])


# ---------------------------------------------------------------------------
# font_plan: role mode
# ---------------------------------------------------------------------------


def test_font_plan_hero_numerals_role_recommends_leco():
    result = font_plan("hero numerals")
    assert result["mode"] == "role"
    assert result["role"] == "hero numerals"
    keys = [r["key"] for r in result["recommendations"]]
    assert "FONT_KEY_LECO_42_NUMBERS" in keys
    assert "FONT_KEY_LECO_36_BOLD_NUMBERS" in keys
    # Every recommendation for this role must actually be a numbers font.
    for r in result["recommendations"]:
        assert r["family"] in ("leco", "bitham")


def test_font_plan_role_aliases_resolve():
    a = font_plan("hero value")
    b = font_plan("hero numerals")
    assert a["role"] == b["role"] == "hero numerals"


def test_font_plan_body_label_role_recommends_gothic():
    result = font_plan("body label")
    assert result["mode"] == "role"
    for r in result["recommendations"]:
        assert r["family"] == "gothic"
        assert r["numbers_only"] is False


def test_font_plan_role_is_case_insensitive():
    assert font_plan("Body Label")["role"] == "body label"


def test_font_plan_role_respects_size_hint_ordering():
    result = font_plan("body label", size_hint=14)
    sizes = [r["size"] for r in result["recommendations"]]
    assert sizes == sorted(sizes, key=lambda s: abs(s - 14))


def test_font_plan_role_respects_style_filter():
    result = font_plan("title", style="bold")
    for r in result["recommendations"]:
        assert "bold" in r["weight"]


# ---------------------------------------------------------------------------
# font_plan: literal text mode
# ---------------------------------------------------------------------------


def test_font_plan_digit_text_prefers_leco_and_fits():
    result = font_plan("15:37")
    assert result["mode"] == "text"
    top = result["recommendations"][0]
    assert top["fits"] is True
    assert top["family"] in ("leco", "bitham")
    assert result["flagged_numbers_only_cant_render"] == []


def test_font_plan_letters_flag_leco_as_non_fitting():
    result = font_plan("Rest 90s")
    assert result["mode"] == "text"
    leco_recs = [r for r in result["recommendations"] if r["family"] == "leco"]
    for r in leco_recs:
        assert r["fits"] is False
        assert set(r["missing_glyphs"]) <= set("Rest 90s")
    # At least one Gothic (full-text) font should fit.
    assert any(r["fits"] for r in result["recommendations"] if r["family"] == "gothic")


def test_font_plan_text_mode_includes_minimal_character_regex():
    result = font_plan("15:37")
    assert result["minimal_character_regex"] == minimal_character_regex("15:37")


def test_font_plan_unknown_role_like_text_falls_back_to_text_mode():
    # Not a recognized role -> treated as literal text.
    result = font_plan("Send")
    assert result["mode"] == "text"
    assert result["input"] == "Send"
