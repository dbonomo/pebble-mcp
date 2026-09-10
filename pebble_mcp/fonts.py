"""System font table, role-based recommendations, and minimal characterRegex
computation for custom fonts.

## Ground truth

The system font key table (``SYSTEM_FONTS`` below) is sourced from the
official Pebble SDK docs, "System Fonts":
https://developer.repebble.com/guides/app-resources/system-fonts/
(fetched 2026-07-31; the page lists every ``FONT_KEY_*`` identifier grouped
by family -- Raster Gothic, Bitham, Roboto/Droid Serif, LECO -- with an
Emery/Basalt preview column). Two LECO keys (``FONT_KEY_LECO_60_NUMBERS_AM_PM``
and ``FONT_KEY_LECO_60_BOLD_NUMBERS_AM_PM``) are explicitly marked "Emery and
newer only" on that page; that is encoded in ``min_platform`` below.

Cross-checked against this repo's own docs:

* ``DESIGN.md`` ("Type & layout"): "Numbers: ``LECO_36_BOLD_NUMBERS`` /
  ``LECO_42_NUMBERS`` for the hero value. Titles/labels: ``GOTHIC_28_BOLD``
  down to ``GOTHIC_14``" -- this is the basis for the ``hero numerals`` /
  ``title`` / ``body label`` role table below.
* ``ECOSYSTEM.md`` ("System fonts"): matches family/size groupings, but lists
  a "Gothic 09" that does **not** appear on the current official system-fonts
  page (which starts at Gothic 14). We follow the live SDK docs -- treat
  ECOSYSTEM.md's "09" as stale/aplite-era and do not expose a Gothic 09 key.
  (Same kind of discrepancy as the GColorMalachite/MediumSpringGreen note in
  ``palette.py`` -- we follow the more authoritative/current source.)

## Glyph coverage caveat

The SDK does not publish an exhaustive glyph-by-glyph table per font key.
"Numbers-only" (LECO, and the Bitham/Roboto variants whose keys say
``_NUMBERS``/``_SUBSET``) is documented informally (ECOSYSTEM.md,
developer.repebble.com font previews) and the glyph sets below are the
**common-usage inference**: digits, plus ``:`` for clock/timer rendering
(every numbers-only font on this watch exists to draw a clock or a timer),
plus ``A``/``M``/``P`` for the ``_AM_PM`` variants (the AM/PM meridiem
marker is letters, not a distinct glyph, despite the "NUMBERS" family name).
``FONT_KEY_ROBOTO_BOLD_SUBSET_49`` is marked numbers-only from its own name
("SUBSET") but its exact glyph list is unconfirmed -- treated conservatively
here; verify visually in the emulator before shipping non-digit text in it.
Full-text families (Gothic, Bitham black/bold/light, Droid Serif, Roboto
Condensed) are treated as covering ordinary printable ASCII text --
``glyphs=None`` below means "assume it fits, no glyph-exact check done".
"""

from __future__ import annotations

from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Ground-truth system font table
# ---------------------------------------------------------------------------

_DIGITS = "0123456789"
_CLOCK_NUMERALS = frozenset(_DIGITS + ":")
_CLOCK_NUMERALS_AM_PM = frozenset(_DIGITS + ":AMP")


@dataclass(frozen=True)
class SystemFont:
    """One system font key baked into Pebble firmware (zero resource cost).

    Attributes:
        key: the C identifier, e.g. ``"FONT_KEY_LECO_42_NUMBERS"``.
        family: ``"gothic"`` | ``"bitham"`` | ``"leco"`` | ``"roboto"`` |
            ``"droid_serif"``.
        size: nominal pixel size parsed from the key.
        weight: ``"regular"`` | ``"bold"`` | ``"light"`` | ``"black"`` |
            ``"medium"`` | ``"condensed"`` | ``"subset"``.
        numbers_only: True for fonts whose key/coverage is restricted to
            digits (+ a handful of clock-related glyphs) -- cannot render
            arbitrary text.
        glyphs: the known/inferred glyph set for numbers-only fonts, or
            ``None`` for full-text fonts (coverage assumed, not glyph-exact).
        min_platform: ``"emery"`` if the SDK docs mark the key
            Emery-and-newer only, else ``None`` (available everywhere).
        note: short citation/caveat for this specific key.
    """

    key: str
    family: str
    size: int
    weight: str
    numbers_only: bool
    glyphs: frozenset[str] | None
    min_platform: str | None
    note: str


SYSTEM_FONTS: list[SystemFont] = [
    # -- Raster Gothic: all UI text (titles, labels, hints). --------------
    SystemFont(
        "FONT_KEY_GOTHIC_14",
        "gothic",
        14,
        "regular",
        False,
        None,
        None,
        'Smallest standard body/hint size; DESIGN.md floor ("nothing smaller").',
    ),
    SystemFont("FONT_KEY_GOTHIC_14_BOLD", "gothic", 14, "bold", False, None, None, ""),
    SystemFont("FONT_KEY_GOTHIC_18", "gothic", 18, "regular", False, None, None, ""),
    SystemFont("FONT_KEY_GOTHIC_18_BOLD", "gothic", 18, "bold", False, None, None, ""),
    SystemFont("FONT_KEY_GOTHIC_24", "gothic", 24, "regular", False, None, None, ""),
    SystemFont("FONT_KEY_GOTHIC_24_BOLD", "gothic", 24, "bold", False, None, None, ""),
    SystemFont("FONT_KEY_GOTHIC_28", "gothic", 28, "regular", False, None, None, ""),
    SystemFont(
        "FONT_KEY_GOTHIC_28_BOLD",
        "gothic",
        28,
        "bold",
        False,
        None,
        None,
        'DESIGN.md\'s title ceiling ("GOTHIC_28_BOLD down to GOTHIC_14").',
    ),
    # -- Bitham: classic Pebble watchface font. ----------------------------
    SystemFont("FONT_KEY_BITHAM_30_BLACK", "bitham", 30, "black", False, None, None, ""),
    SystemFont(
        "FONT_KEY_BITHAM_34_MEDIUM_NUMBERS",
        "bitham",
        34,
        "medium",
        True,
        _CLOCK_NUMERALS,
        None,
        "Numbers-only per key name; digits + colon inferred.",
    ),
    SystemFont("FONT_KEY_BITHAM_42_BOLD", "bitham", 42, "bold", False, None, None, ""),
    SystemFont("FONT_KEY_BITHAM_42_LIGHT", "bitham", 42, "light", False, None, None, ""),
    SystemFont(
        "FONT_KEY_BITHAM_42_MEDIUM_NUMBERS",
        "bitham",
        42,
        "medium",
        True,
        _CLOCK_NUMERALS,
        None,
        "Numbers-only per key name; digits + colon inferred.",
    ),
    # -- Roboto / Droid Serif: 2013-era leftovers (ECOSYSTEM.md). ----------
    SystemFont("FONT_KEY_ROBOTO_CONDENSED_21", "roboto", 21, "condensed", False, None, None, ""),
    SystemFont(
        "FONT_KEY_ROBOTO_BOLD_SUBSET_49",
        "roboto",
        49,
        "subset",
        True,
        frozenset(_DIGITS + ":-°"),
        None,
        "Name says SUBSET; glyph list unconfirmed by SDK docs -- treated "
        "conservatively as numbers-only (digits/colon/minus/degree). Verify "
        "in-emulator before relying on it for non-digit text.",
    ),
    SystemFont("FONT_KEY_DROID_SERIF_28_BOLD", "droid_serif", 28, "bold", False, None, None, ""),
    # -- LECO: numbers-only hero digits (clocks, timers, weights). ---------
    SystemFont(
        "FONT_KEY_LECO_20_BOLD_NUMBERS",
        "leco",
        20,
        "bold",
        True,
        _CLOCK_NUMERALS,
        None,
        "Numbers-only.",
    ),
    SystemFont(
        "FONT_KEY_LECO_26_BOLD_NUMBERS_AM_PM",
        "leco",
        26,
        "bold",
        True,
        _CLOCK_NUMERALS_AM_PM,
        None,
        "Numbers-only; _AM_PM variant adds A/M/P letters for the meridiem marker.",
    ),
    SystemFont(
        "FONT_KEY_LECO_28_LIGHT_NUMBERS",
        "leco",
        28,
        "light",
        True,
        _CLOCK_NUMERALS,
        None,
        "Numbers-only.",
    ),
    SystemFont(
        "FONT_KEY_LECO_32_BOLD_NUMBERS",
        "leco",
        32,
        "bold",
        True,
        _CLOCK_NUMERALS,
        None,
        "Numbers-only.",
    ),
    SystemFont(
        "FONT_KEY_LECO_36_BOLD_NUMBERS",
        "leco",
        36,
        "bold",
        True,
        _CLOCK_NUMERALS,
        None,
        "Numbers-only; DESIGN.md hero-value default (with LECO_42).",
    ),
    SystemFont(
        "FONT_KEY_LECO_38_BOLD_NUMBERS",
        "leco",
        38,
        "bold",
        True,
        _CLOCK_NUMERALS,
        None,
        "Numbers-only.",
    ),
    SystemFont(
        "FONT_KEY_LECO_42_NUMBERS",
        "leco",
        42,
        "regular",
        True,
        _CLOCK_NUMERALS,
        None,
        "Numbers-only; DESIGN.md hero-value default (with LECO_36_BOLD).",
    ),
    SystemFont(
        "FONT_KEY_LECO_60_NUMBERS_AM_PM",
        "leco",
        60,
        "regular",
        True,
        _CLOCK_NUMERALS_AM_PM,
        "emery",
        "Numbers-only, Emery and newer only per SDK docs.",
    ),
    SystemFont(
        "FONT_KEY_LECO_60_BOLD_NUMBERS_AM_PM",
        "leco",
        60,
        "bold",
        True,
        _CLOCK_NUMERALS_AM_PM,
        "emery",
        "Numbers-only, Emery and newer only per SDK docs.",
    ),
]

_BY_KEY: dict[str, SystemFont] = {f.key: f for f in SYSTEM_FONTS}


def all_fonts() -> list[SystemFont]:
    """Return the full system font table (a copy of the module list)."""
    return list(SYSTEM_FONTS)


def get_font(key: str) -> SystemFont | None:
    """Look up a system font by its ``FONT_KEY_*`` identifier."""
    return _BY_KEY.get(key)


# ---------------------------------------------------------------------------
# Role -> recommended font keys, curated from DESIGN.md's standardized usage.
# ---------------------------------------------------------------------------

ROLE_RECOMMENDATIONS: dict[str, list[str]] = {
    "hero numerals": [
        "FONT_KEY_LECO_42_NUMBERS",
        "FONT_KEY_LECO_36_BOLD_NUMBERS",
        "FONT_KEY_LECO_38_BOLD_NUMBERS",
        "FONT_KEY_BITHAM_42_MEDIUM_NUMBERS",
    ],
    "timer": [
        "FONT_KEY_LECO_38_BOLD_NUMBERS",
        "FONT_KEY_LECO_42_NUMBERS",
        "FONT_KEY_LECO_36_BOLD_NUMBERS",
    ],
    "clock face numerals": [
        "FONT_KEY_LECO_60_BOLD_NUMBERS_AM_PM",
        "FONT_KEY_LECO_60_NUMBERS_AM_PM",
        "FONT_KEY_BITHAM_42_MEDIUM_NUMBERS",
        "FONT_KEY_BITHAM_34_MEDIUM_NUMBERS",
    ],
    "title": [
        "FONT_KEY_GOTHIC_28_BOLD",
        "FONT_KEY_GOTHIC_24_BOLD",
    ],
    "body label": [
        "FONT_KEY_GOTHIC_18",
        "FONT_KEY_GOTHIC_14",
    ],
    "hint": [
        "FONT_KEY_GOTHIC_14",
        "FONT_KEY_GOTHIC_18",
    ],
    "button label": [
        "FONT_KEY_GOTHIC_18_BOLD",
        "FONT_KEY_GOTHIC_14_BOLD",
    ],
}

# Synonyms that map onto a canonical role above (normalized: lower, stripped).
_ROLE_ALIASES: dict[str, str] = {
    "hero value": "hero numerals",
    "hero": "hero numerals",
    "big number": "hero numerals",
    "big numerals": "hero numerals",
    "weight": "hero numerals",
    "stopwatch": "timer",
    "countdown": "timer",
    "clock": "clock face numerals",
    "watchface numerals": "clock face numerals",
    "heading": "title",
    "header": "title",
    "label": "body label",
    "body": "body label",
    "body text": "body label",
    "subtitle": "hint",
    "caption": "hint",
    "button": "button label",
}


def _match_role(text: str) -> str | None:
    norm = text.strip().lower()
    if norm in ROLE_RECOMMENDATIONS:
        return norm
    if norm in _ROLE_ALIASES:
        return _ROLE_ALIASES[norm]
    return None


# ---------------------------------------------------------------------------
# minimal_character_regex -- the TTMM trick: ship only the glyphs used.
# ---------------------------------------------------------------------------

# Characters that are special inside a `[...]` regex character class and must
# be escaped when used as a literal or a range endpoint. Besides the four that
# can change what the class matches (``\ ] ^ -``), we also escape ``[``: a bare
# ``[`` inside a class is a valid literal today but makes Python's ``re`` emit a
# "Possible nested set" ``FutureWarning`` (a scheduled future error) whenever it
# is followed by another char, so escaping it keeps the output a clean, warning-
# free, forward-compatible regex for every input.
_CLASS_SPECIALS = frozenset("\\]^-[")


def _escape_for_class(ch: str) -> str:
    return "\\" + ch if ch in _CLASS_SPECIALS else ch


def minimal_character_regex(text: str) -> str:
    """Compute the tightest ``characterRegex`` covering exactly the glyphs in
    ``text``, for the Pebble ``package.json`` custom-font ``characterRegex``
    field (Python regex; see ECOSYSTEM.md "Custom fonts" and
    https://developer.repebble.com/guides/app-resources/fonts/).

    Deduplicates, sorts by codepoint, collapses runs of 3+ consecutive
    codepoints into a ``a-z``-style range (matching the classic ``"[0-9:]"``
    clock-digit example from the docs), and escapes character-class-special
    characters (``\\ ] ^ - [``) so the result is always a valid, warning-free
    character class for **any** input (including control characters, combining
    marks, and astral-plane emoji -- fuzz-tested in ``tests/test_fonts.py``).

    Single characters and 2-character runs are kept as literals rather than
    collapsed into a range -- a range only pays for itself at 3+ chars, and
    listing 2 chars individually is exactly as tight and easier to read.
    """
    if not text:
        raise ValueError(
            "font_plan needs a non-empty role_or_text: pass a known role name "
            "(e.g. 'hero numerals', 'title', 'body label') or literal text to render"
        )

    chars = sorted(set(text), key=ord)
    parts: list[str] = []
    i = 0
    n = len(chars)
    while i < n:
        j = i
        while j + 1 < n and ord(chars[j + 1]) == ord(chars[j]) + 1:
            j += 1
        run_len = j - i + 1
        if run_len >= 3:
            parts.append(f"{_escape_for_class(chars[i])}-{_escape_for_class(chars[j])}")
        else:
            parts.extend(_escape_for_class(c) for c in chars[i : j + 1])
        i = j + 1

    return "[" + "".join(parts) + "]"


# ---------------------------------------------------------------------------
# font_plan -- the public recommendation entry point.
# ---------------------------------------------------------------------------


def _font_summary(
    f: SystemFont, *, fits: bool | None = None, missing: list[str] | None = None
) -> dict:
    d = {
        "key": f.key,
        "family": f.family,
        "size": f.size,
        "weight": f.weight,
        "numbers_only": f.numbers_only,
        "min_platform": f.min_platform,
        "note": f.note,
    }
    if fits is not None:
        d["fits"] = fits
        d["missing_glyphs"] = missing or []
    return d


def _fits_text(f: SystemFont, text: str) -> tuple[bool, list[str]]:
    if f.glyphs is None:
        return True, []
    missing = sorted({c for c in text if c not in f.glyphs})
    return (len(missing) == 0, missing)


def font_plan(
    role_or_text: str,
    *,
    size_hint: int | None = None,
    style: str | None = None,
) -> dict:
    """Recommend system fonts for a role (e.g. ``"hero numerals"``, ``"body
    label"``) or a literal string to render.

    Role mode: looks ``role_or_text`` up (case-insensitive, with common
    synonyms) in :data:`ROLE_RECOMMENDATIONS`, curated from DESIGN.md's
    standardized type scale. Returns the ordered candidate list, optionally
    filtered/sorted by ``size_hint`` (closest nominal size first) and
    ``style`` (a weight substring, e.g. ``"bold"``).

    Text mode: if ``role_or_text`` doesn't match a known role, it is treated
    as literal text to render. Every system font is checked for glyph
    coverage; numbers-only fonts (LECO and friends) that can't render the
    text are flagged via ``fits: false`` + ``missing_glyphs`` rather than
    silently recommended. Always includes ``minimal_character_regex`` -- the
    tight ``characterRegex`` for a custom font, useful whether or not a
    system font already fits (e.g. to standardize on a house numeral face).

    Returns a dict; see the ``mode`` key for which shape applies.
    """
    role_key = _match_role(role_or_text)

    if role_key is not None:
        keys = ROLE_RECOMMENDATIONS[role_key]
        candidates = [_BY_KEY[k] for k in keys if k in _BY_KEY]
        if style:
            style_norm = style.strip().lower()
            filtered = [f for f in candidates if style_norm in f.weight]
            if filtered:
                candidates = filtered
        if size_hint is not None:
            candidates = sorted(candidates, key=lambda f: abs(f.size - size_hint))
        return {
            "mode": "role",
            "role": role_key,
            "recommendations": [_font_summary(f) for f in candidates],
        }

    text = role_or_text
    scored: list[tuple[bool, int, SystemFont, list[str]]] = []
    for f in SYSTEM_FONTS:
        if f.min_platform not in (None, "emery"):  # pragma: no cover - no such entries today
            continue
        fits, missing = _fits_text(f, text)
        size_gap = abs(f.size - size_hint) if size_hint is not None else 0
        scored.append((fits, size_gap, f, missing))

    # Best fits first, then closest to size_hint, then larger-size-first as a
    # tiebreak (bigger hero numerals read better at arm's length -- DESIGN.md).
    scored.sort(key=lambda t: (not t[0], t[1], -t[2].size))

    recommendations = [
        _font_summary(f, fits=fits, missing=missing) for fits, _gap, f, missing in scored[:6]
    ]
    flagged_numbers_only = [
        r["key"] for r in recommendations if r["numbers_only"] and not r["fits"]
    ]

    return {
        "mode": "text",
        "input": text,
        "recommendations": recommendations,
        "flagged_numbers_only_cant_render": flagged_numbers_only,
        "minimal_character_regex": minimal_character_regex(text),
    }
