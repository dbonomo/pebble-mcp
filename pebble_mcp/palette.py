"""The Pebble 64-color palette: ground-truth table, nearest-color matching,
WCAG contrast math, and labeled swatch rendering.

This is the core of the Tier 2 design toolkit. Everything here is pure Python
plus Pillow (swatch rendering only); no toolchain or network needed.

## The palette

Pebble color displays are 2 bits per RGB channel: each channel is exactly one
of ``00``, ``55``, ``AA``, ``FF``. That is the entire gamut -- 64 colors. Every
one has an official ``GColor*`` name in the SDK and a packed 8-bit ARGB value
(``GColor8``): bits ``[7:6]`` alpha, ``[5:4]`` red, ``[3:2]`` green, ``[1:0]``
blue, each channel a 2-bit code (00->0, 55->1, AA->2, FF->3). Opaque colors
have alpha ``0b11``, so e.g. ``GColorWhite`` is ``0b11111111`` = ``0xFF`` and
``GColorVividCerulean`` (00AAFF) is ``0b11001011`` = ``0xCB``.

## How PALETTE_DATA was derived (and how the tests re-verify it)

The embedded ``PALETTE_DATA`` table is *generated* from the vendored ground
truth under ``assets/palettes/`` and embedded so the installed package never
depends on files outside itself. Three sources are cross-checked:

* ``pebble_colors_64.act`` (Photoshop color table) -- 256 RGB triplets plus a
  trailer ``00 40 ff ff`` (count=64, no transparency). The first 64 triplets
  give the canonical color *order* used here (matches DESIGN.md's 8x8 grid).
* ``pebble_colors_uncorrected.aseprite`` / ``pebble_colors_sunlight.aseprite``
  -- two Aseprite files sharing one 114-slot layout (64 real entries with
  alpha=255, 50 padding entries with alpha=0). Index-aligned between the two
  files, so the sunlight file yields the sunlight-corrected display hex for
  each raw color. The correction models how the reflective LCD actually looks
  (DESIGN.md: "the emulator lies bright").
* GColor names: the official SDK ``GColor*`` identifiers from the Pebble
  Color Definitions docs (developer.repebble.com). Spot-checked against
  DESIGN.md's named colors: VividCerulean=00AAFF, ChromeYellow=FFAA00,
  Green=00FF00, LightGray=AAAAAA, DarkGray=555555.

  NOTE: some third-party palette write-ups label **00FF55** as
  "GColorMediumSpringGreen"; that is a mislabel. The SDK assigns
  **00FF55 -> GColorMalachite** and **00FFAA -> GColorMediumSpringGreen**, and
  we follow the SDK — 00FF55 is correctly named GColorMalachite here.

The generator lives at ``scripts/gen_palette.py``. ``rgb`` and ``argb8`` below
are derived arithmetically at import (they are pure functions of the hex), so
the embedded table only carries name + hex + corrected hex. The golden test
``tests/test_palette.py`` re-parses the three vendored files and asserts the
embedded table matches byte-for-byte, so drift is caught in CI.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from typing import NamedTuple

# ---------------------------------------------------------------------------
# Ground-truth table (generated -- see module docstring and scripts/gen_palette.py)
# (GColor name, uncorrected hex, sunlight-corrected display hex), canonical order.
# ---------------------------------------------------------------------------
PALETTE_DATA: list[tuple[str, str, str]] = [
    ("GColorBlack", "000000", "000000"),
    ("GColorOxfordBlue", "000055", "001E41"),
    ("GColorDukeBlue", "0000AA", "004387"),
    ("GColorBlue", "0000FF", "0068CA"),
    ("GColorDarkGreen", "005500", "2B4A2C"),
    ("GColorMidnightGreen", "005555", "27514F"),
    ("GColorCobaltBlue", "0055AA", "16638D"),
    ("GColorBlueMoon", "0055FF", "007DCE"),
    ("GColorIslamicGreen", "00AA00", "5E9860"),
    ("GColorJaegerGreen", "00AA55", "5C9B72"),
    ("GColorTiffanyBlue", "00AAAA", "57A5A2"),
    ("GColorVividCerulean", "00AAFF", "4CB4DB"),
    ("GColorGreen", "00FF00", "8EE391"),
    ("GColorMalachite", "00FF55", "8EE69E"),
    ("GColorMediumSpringGreen", "00FFAA", "8AEBC0"),
    ("GColorCyan", "00FFFF", "84F5F1"),
    ("GColorBulgarianRose", "550000", "4A161B"),
    ("GColorImperialPurple", "550055", "482748"),
    ("GColorIndigo", "5500AA", "40488A"),
    ("GColorElectricUltramarine", "5500FF", "2F6BCC"),
    ("GColorArmyGreen", "555500", "564E36"),
    ("GColorDarkGray", "555555", "545454"),
    ("GColorLiberty", "5555AA", "4F6790"),
    ("GColorVeryLightBlue", "5555FF", "4180D0"),
    ("GColorKellyGreen", "55AA00", "759A64"),
    ("GColorMayGreen", "55AA55", "759D76"),
    ("GColorCadetBlue", "55AAAA", "71A6A4"),
    ("GColorPictonBlue", "55AAFF", "69B5DD"),
    ("GColorBrightGreen", "55FF00", "9EE594"),
    ("GColorScreaminGreen", "55FF55", "9DE7A0"),
    ("GColorMediumAquamarine", "55FFAA", "9BECC2"),
    ("GColorElectricBlue", "55FFFF", "95F6F2"),
    ("GColorDarkCandyAppleRed", "AA0000", "99353F"),
    ("GColorJazzberryJam", "AA0055", "983E5A"),
    ("GColorPurple", "AA00AA", "955694"),
    ("GColorVividViolet", "AA00FF", "8F74D2"),
    ("GColorWindsorTan", "AA5500", "9D5B4D"),
    ("GColorRoseVale", "AA5555", "9D6064"),
    ("GColorPurpureus", "AA55AA", "9A7099"),
    ("GColorLavenderIndigo", "AA55FF", "9587D5"),
    ("GColorLimerick", "AAAA00", "AFA072"),
    ("GColorBrass", "AAAA55", "AEA382"),
    ("GColorLightGray", "AAAAAA", "ABABAB"),
    ("GColorBabyBlueEyes", "AAAAFF", "A7BAE2"),
    ("GColorSpringBud", "AAFF00", "C9E89D"),
    ("GColorInchworm", "AAFF55", "C9EAA7"),
    ("GColorMintGreen", "AAFFAA", "C7F0C8"),
    ("GColorCeleste", "AAFFFF", "C3F9F7"),
    ("GColorRed", "FF0000", "E35462"),
    ("GColorFolly", "FF0055", "E25874"),
    ("GColorFashionMagenta", "FF00AA", "E16AA3"),
    ("GColorMagenta", "FF00FF", "DE83DC"),
    ("GColorOrange", "FF5500", "E66E6B"),
    ("GColorSunsetOrange", "FF5555", "E6727C"),
    ("GColorBrilliantRose", "FF55AA", "E37FA7"),
    ("GColorShockingPink", "FF55FF", "E194DF"),
    ("GColorChromeYellow", "FFAA00", "F1AA86"),
    ("GColorRajah", "FFAA55", "F1AD93"),
    ("GColorMelon", "FFAAAA", "EFB5B8"),
    ("GColorRichBrilliantLavender", "FFAAFF", "ECC3EB"),
    ("GColorYellow", "FFFF00", "FFEEAB"),
    ("GColorIcterine", "FFFF55", "FFF1B5"),
    ("GColorPastelYellow", "FFFFAA", "FFF6D3"),
    ("GColorWhite", "FFFFFF", "FFFFFF"),
]

# 2-bit channel code: raw 8-bit channel value -> 0..3
_CHANNEL_CODE = {0x00: 0, 0x55: 1, 0xAA: 2, 0xFF: 3}

RGB = tuple[int, int, int]


def _hex_to_rgb(h: str) -> RGB:
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


def _argb8(rgb: RGB) -> int:
    """Packed opaque GColor8 byte for a palette color (alpha = 0b11)."""
    r, g, b = rgb
    return 0b11000000 | (_CHANNEL_CODE[r] << 4) | (_CHANNEL_CODE[g] << 2) | _CHANNEL_CODE[b]


@dataclass(frozen=True)
class PaletteColor:
    """One of the 64 Pebble palette colors.

    Attributes:
        name: official SDK ``GColor*`` identifier (also the C constant to use).
        hex: 6-digit uppercase RGB hex, no leading ``#`` (the SDK/uncorrected value).
        rgb: ``(r, g, b)`` 0-255, each channel one of 0/85/170/255.
        argb8: packed opaque ``GColor8`` byte (e.g. white -> 0xFF). Also
            expressible in C as ``GColor<name>ARGB8``.
        corrected_hex: sunlight-corrected display hex -- how the reflective LCD
            actually renders this color (from the sunlight-corrected palette).
    """

    name: str
    hex: str
    rgb: RGB
    argb8: int
    corrected_hex: str

    @property
    def c_constant(self) -> str:
        """The C constant you write in a Pebble app (identical to ``name``)."""
        return self.name


PALETTE: list[PaletteColor] = [
    PaletteColor(name, h, _hex_to_rgb(h), _argb8(_hex_to_rgb(h)), corrected)
    for (name, h, corrected) in PALETTE_DATA
]

_BY_HEX: dict[str, PaletteColor] = {c.hex: c for c in PALETTE}
_BY_NAME: dict[str, PaletteColor] = {c.name: c for c in PALETTE}
_BY_ARGB8: dict[int, PaletteColor] = {c.argb8: c for c in PALETTE}


class Match(NamedTuple):
    """Result of :func:`nearest`."""

    exact: bool
    name: str
    hex: str
    c_constant: str
    corrected_hex: str
    distance: float


# ---------------------------------------------------------------------------
# Color parsing / lookup
# ---------------------------------------------------------------------------
def parse_color(color: str | RGB | PaletteColor) -> RGB:
    """Coerce a hex string, ``(r, g, b)`` tuple, or PaletteColor to an RGB tuple.

    Accepts ``"#RRGGBB"``, ``"RRGGBB"``, ``"#RGB"``, or ``"RGB"`` shorthand.
    """
    if isinstance(color, PaletteColor):
        return color.rgb
    if isinstance(color, str):
        h = color.strip().lstrip("#")
        if len(h) == 3:
            h = "".join(ch * 2 for ch in h)
        if len(h) != 6:
            raise ValueError(
                f"invalid hex color: {color!r}; expected '#RRGGBB', 'RRGGBB', "
                "or the 3-digit '#RGB'/'RGB' shorthand"
            )
        try:
            return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))
        except ValueError as exc:
            raise ValueError(
                f"invalid hex color: {color!r}; digits after '#' must be hexadecimal (0-9, a-f)"
            ) from exc
    r, g, b = color  # type: ignore[misc]
    for v in (r, g, b):
        if not (0 <= int(v) <= 255):
            raise ValueError(
                f"RGB channel out of range: {color!r}; each of r/g/b must be an integer 0-255"
            )
    return (int(r), int(g), int(b))


def rgb_to_hex(rgb: RGB) -> str:
    """``(r, g, b)`` -> ``"RRGGBB"`` (uppercase, no ``#``)."""
    return "{:02X}{:02X}{:02X}".format(*rgb)


def get(color: str | RGB) -> PaletteColor | None:
    """Return the exact PaletteColor for a hex/rgb value, or ``None`` if it is
    not one of the 64 palette entries."""
    return _BY_HEX.get(rgb_to_hex(parse_color(color)))


def from_argb8(byte: int) -> PaletteColor | None:
    """Return the PaletteColor for a packed opaque ``GColor8`` byte, or None."""
    return _BY_ARGB8.get(byte)


# ---------------------------------------------------------------------------
# Nearest-color matching
# ---------------------------------------------------------------------------
def nearest(color: str | RGB) -> Match:
    """Find the nearest of the 64 Pebble colors to an arbitrary color.

    Distance is straight Euclidean distance in sRGB space -- simple, symmetric,
    and good enough for "which palette entry is this closest to". ``exact`` is
    True when ``color`` is already a palette entry (distance 0).

    Args:
        color: a hex string (``"#RRGGBB"`` / ``"RRGGBB"`` / shorthand) or an
            ``(r, g, b)`` tuple.

    Returns:
        Match(exact, name, hex, c_constant, corrected_hex, distance).
    """
    tr, tg, tb = parse_color(color)
    best: PaletteColor | None = None
    best_d2 = -1
    for c in PALETTE:
        r, g, b = c.rgb
        d2 = (r - tr) ** 2 + (g - tg) ** 2 + (b - tb) ** 2
        if best is None or d2 < best_d2:
            best, best_d2 = c, d2
    assert best is not None
    return Match(
        exact=best_d2 == 0,
        name=best.name,
        hex=best.hex,
        c_constant=best.c_constant,
        corrected_hex=best.corrected_hex,
        distance=round(best_d2**0.5, 3),
    )


# ---------------------------------------------------------------------------
# WCAG contrast
# ---------------------------------------------------------------------------
def _relative_luminance(rgb: RGB) -> float:
    """WCAG 2.x relative luminance of an sRGB color."""

    def lin(c: int) -> float:
        s = c / 255.0
        return s / 12.92 if s <= 0.03928 else ((s + 0.055) / 1.055) ** 2.4

    r, g, b = rgb
    return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)


def contrast_ratio(c1: str | RGB, c2: str | RGB) -> float:
    """WCAG contrast ratio between two colors, in ``[1.0, 21.0]``.

    ``(L1 + 0.05) / (L2 + 0.05)`` with L1 the lighter luminance. Order of
    arguments does not matter.
    """
    l1 = _relative_luminance(parse_color(c1))
    l2 = _relative_luminance(parse_color(c2))
    lighter, darker = max(l1, l2), min(l1, l2)
    return round((lighter + 0.05) / (darker + 0.05), 3)


# Legibility threshold for the Pebble always-on reflective LCD.
#
# WCAG's reference thresholds are 4.5:1 (AA, normal text), 3:1 (AA large/bold
# text), 7:1 (AAA). We default to **4.5:1** -- the AA normal-text bar -- as the
# floor for the design toolkit, for reasons specific to this ~1.5" reflective
# display:
#   * DESIGN.md: "Contrast is king ... the emulator lies bright" -- saturated
#     palette entries read 1-2 steps more muted on the real reflective LCD than
#     the computed sRGB contrast suggests, so a conservative floor is warranted.
#   * The screen is small and often read in bad gym lighting with the backlight
#     off, which erodes effective contrast further.
#   * Hero numerals (big bold LECO fonts) genuinely qualify as "large text" and
#     can pass at 3:1; callers can lower the threshold for those via the
#     ``threshold`` argument. Body hints/labels should hold the 4.5 line.
# Judge legibility against the *corrected* hex when you can (that models the
# real display), not the bright uncorrected value.
LEGIBLE_THRESHOLD = 4.5
LEGIBLE_THRESHOLD_LARGE = 3.0


def legible(fg: str | RGB, bg: str | RGB, threshold: float = LEGIBLE_THRESHOLD) -> bool:
    """Whether ``fg`` on ``bg`` clears the contrast ``threshold`` (default 4.5).

    Pass ``threshold=LEGIBLE_THRESHOLD_LARGE`` (3.0) for large/bold hero text.
    """
    return contrast_ratio(fg, bg) >= threshold


# ---------------------------------------------------------------------------
# Swatch rendering
# ---------------------------------------------------------------------------
# Upper bound on how many cells a single swatch will render. The palette itself
# is only 64 colors; this leaves generous headroom for eyeballing off-palette
# candidate sets while keeping the output image (and its memory/time cost)
# bounded no matter what a caller passes.
MAX_SWATCH_COLORS = 256


def swatch(
    colors: list[str | RGB | PaletteColor],
    labels: list[str] | None = None,
    *,
    scale: int = 2,
    columns: int | None = None,
) -> bytes:
    """Render a labeled grid of color swatches to PNG bytes.

    Works for 1..``MAX_SWATCH_COLORS`` (256) colors; more than that raises
    ``ValueError`` rather than rendering an unbounded image. Each cell is the
    flat color block with a caption (its label, or the auto hex) drawn in
    whichever of black/white is more legible on that color.

    Args:
        colors: hex strings, ``(r, g, b)`` tuples, and/or PaletteColors.
        labels: optional captions, one per color. Defaults to each color's
            palette name if it is an exact palette entry, else its hex.
        scale: integer upscale factor for crisp pixels (default 2).
        columns: cells per row. Defaults to ``min(8, n)`` (the palette is 8x8).

    Returns:
        PNG-encoded image bytes.
    """
    from PIL import Image, ImageDraw, ImageFont

    if not colors:
        raise ValueError("palette_swatch needs at least one color; got an empty list")
    if len(colors) > MAX_SWATCH_COLORS:
        raise ValueError(
            f"palette_swatch renders at most {MAX_SWATCH_COLORS} colors; got {len(colors)} "
            "-- pass fewer colors or split into multiple swatches"
        )
    if labels is not None and len(labels) != len(colors):
        raise ValueError(
            f"labels must match colors in length: got {len(labels)} labels for "
            f"{len(colors)} colors"
        )

    rgbs = [parse_color(c) for c in colors]
    if labels is None:
        caps: list[str] = []
        for c, rgb in zip(colors, rgbs, strict=True):
            entry = c if isinstance(c, PaletteColor) else _BY_HEX.get(rgb_to_hex(rgb))
            caps.append(entry.name if entry is not None else "#" + rgb_to_hex(rgb))
    else:
        caps = list(labels)

    n = len(rgbs)
    cols = columns if columns is not None else min(8, n)
    cols = max(1, cols)
    rows = (n + cols - 1) // cols

    # Base (1x) geometry, then scale up.
    cell_w, block_h, caption_h, pad = 92, 40, 16, 2
    cell_h = block_h + caption_h
    img_w = cols * cell_w + pad * 2
    img_h = rows * cell_h + pad * 2

    img = Image.new("RGB", (img_w * scale, img_h * scale), (255, 255, 255))
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.load_default(size=11 * scale)
    except TypeError:  # very old Pillow: load_default() takes no size
        font = ImageFont.load_default()

    for i, (rgb, cap) in enumerate(zip(rgbs, caps, strict=True)):
        col, row = i % cols, i // cols
        x0 = (pad + col * cell_w) * scale
        y0 = (pad + row * cell_h) * scale
        x1 = x0 + cell_w * scale
        yb = y0 + block_h * scale
        draw.rectangle([x0, y0, x1 - 1, yb - 1], fill=rgb)

        # Caption text in the more-legible of black/white over the block color.
        text_color = (0, 0, 0) if contrast_ratio(rgb, (0, 0, 0)) >= contrast_ratio(
            rgb, (255, 255, 255)
        ) else (255, 255, 255)
        _centered_text(draw, cap, x0, y0, cell_w * scale, block_h * scale, font, text_color)

        # Hex caption below the block, always black on the white margin.
        hexcap = "#" + rgb_to_hex(rgb)
        _centered_text(
            draw, hexcap, x0, yb, cell_w * scale, caption_h * scale, font, (30, 30, 30)
        )

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _centered_text(draw, text: str, x: int, y: int, w: int, h: int, font, fill) -> None:
    try:
        bbox = draw.textbbox((0, 0), text, font=font)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        off_x, off_y = bbox[0], bbox[1]
    except AttributeError:  # pragma: no cover - ancient Pillow
        tw, th = draw.textsize(text, font=font)
        off_x = off_y = 0
    draw.text(
        (x + (w - tw) / 2 - off_x, y + (h - th) / 2 - off_y),
        text,
        font=font,
        fill=fill,
    )
