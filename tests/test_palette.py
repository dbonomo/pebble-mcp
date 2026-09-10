"""Golden tests for pebble_mcp.palette.

The embedded PALETTE_DATA table is verified against the vendored ground-truth
files in assets/palettes/ (the .act color table and both .aseprite variants),
so any drift between the code and the real palette data fails here.
"""

from __future__ import annotations

import io
import struct
from pathlib import Path

import pytest

from pebble_mcp import palette
from pebble_mcp.palette import (
    LEGIBLE_THRESHOLD_LARGE,
    PALETTE,
    Match,
    contrast_ratio,
    from_argb8,
    get,
    legible,
    nearest,
    parse_color,
    swatch,
)

# Ground-truth palette files, vendored in the package repo so the suite is
# self-contained: tests/ -> pebble-mcp/ -> assets/palettes.
ASSETS = Path(__file__).resolve().parents[1] / "assets" / "palettes"


# ---------------------------------------------------------------------------
# Ground-truth parsers (independent re-implementation for the golden checks).
# ---------------------------------------------------------------------------
def _parse_act(path: Path) -> list[str]:
    data = path.read_bytes()
    count = struct.unpack(">H", data[768:770])[0]
    return [f"{data[i * 3]:02X}{data[i * 3 + 1]:02X}{data[i * 3 + 2]:02X}" for i in range(count)]


def _parse_aseprite(path: Path) -> list[tuple[int, int, int, int]]:
    data = path.read_bytes()
    p = 128 + 16
    while p < len(data) - 6:
        csize, ctype = struct.unpack("<IH", data[p : p + 6])
        if ctype == 0x2019:
            cdata = data[p + 6 : p + csize]
            _, first, last = struct.unpack("<III", cdata[:12])
            q, out = 20, []
            for _ in range(first, last + 1):
                (flags,) = struct.unpack("<H", cdata[q : q + 2])
                q += 2
                r, g, b, a = cdata[q], cdata[q + 1], cdata[q + 2], cdata[q + 3]
                q += 4
                if flags & 1:
                    (nlen,) = struct.unpack("<H", cdata[q : q + 2])
                    q += 2 + nlen
                out.append((r, g, b, a))
            return out
        p += csize
    raise ValueError("no palette chunk")


# ---------------------------------------------------------------------------
# Table shape and derivation
# ---------------------------------------------------------------------------
def test_palette_has_64_unique_colors():
    assert len(PALETTE) == 64
    assert len({c.hex for c in PALETTE}) == 64
    assert len({c.name for c in PALETTE}) == 64
    assert len({c.argb8 for c in PALETTE}) == 64


def test_only_2bit_channels():
    for c in PALETTE:
        for ch in c.rgb:
            assert ch in (0x00, 0x55, 0xAA, 0xFF)


def test_embedded_order_matches_act_bytes():
    order = _parse_act(ASSETS / "pebble_colors_64.act")[:64]
    assert [c.hex for c in PALETTE] == order


def test_corrected_hex_matches_sunlight_aseprite():
    unc = _parse_aseprite(ASSETS / "pebble_colors_uncorrected.aseprite")
    sun = _parse_aseprite(ASSETS / "pebble_colors_sunlight.aseprite")
    corrected = {
        f"{u[0]:02X}{u[1]:02X}{u[2]:02X}": f"{s[0]:02X}{s[1]:02X}{s[2]:02X}"
        for u, s in zip(unc, sun, strict=True)
        if u[3] == 255
    }
    assert len(corrected) == 64
    for c in PALETTE:
        assert c.corrected_hex == corrected[c.hex], c.name


# ---------------------------------------------------------------------------
# Round-trips: byte value <-> rgb <-> hex
# ---------------------------------------------------------------------------
def test_roundtrip_all_entries():
    code = {0x00: 0, 0x55: 1, 0xAA: 2, 0xFF: 3}
    for c in PALETTE:
        r, g, b = c.rgb
        # rgb -> hex
        assert f"{r:02X}{g:02X}{b:02X}" == c.hex
        # hex -> rgb
        assert parse_color(c.hex) == c.rgb
        # rgb -> argb8 (opaque, alpha 0b11)
        expected = 0b11000000 | (code[r] << 4) | (code[g] << 2) | code[b]
        assert c.argb8 == expected
        # argb8 -> back to the same entry
        assert from_argb8(c.argb8) is c
        # argb8 high two bits are the opaque-alpha marker
        assert c.argb8 >> 6 == 0b11


def test_known_argb8_values():
    assert get("000000").argb8 == 0xC0  # Black, opaque
    assert get("FFFFFF").argb8 == 0xFF  # White
    assert get("00AAFF").argb8 == 0xCB  # VividCerulean
    assert get("FFAA00").argb8 == 0xF8  # ChromeYellow


# ---------------------------------------------------------------------------
# Named colors match DESIGN.md
# ---------------------------------------------------------------------------
def test_named_colors_match_design_md():
    # Colors where the SDK name and DESIGN.md agree.
    expected = {
        "000000": "GColorBlack",
        "FFFFFF": "GColorWhite",
        "AAAAAA": "GColorLightGray",
        "555555": "GColorDarkGray",
        "00AAFF": "GColorVividCerulean",
        "00FF00": "GColorGreen",
        "FFAA00": "GColorChromeYellow",
    }
    for h, name in expected.items():
        assert get(h).name == name


def test_malachite_vs_mediumspringgreen():
    # DESIGN.md line 39 labels 00FF55 "GColorMediumSpringGreen"; the SDK is the
    # authority and names 00FF55 Malachite, 00FFAA MediumSpringGreen.
    assert get("00FF55").name == "GColorMalachite"
    assert get("00FFAA").name == "GColorMediumSpringGreen"


# ---------------------------------------------------------------------------
# nearest()
# ---------------------------------------------------------------------------
def test_nearest_exact_hits():
    for c in PALETTE:
        for form in (c.hex, "#" + c.hex, c.rgb):
            m = nearest(form)
            assert isinstance(m, Match)
            assert m.exact is True
            assert m.hex == c.hex
            assert m.name == c.name
            assert m.c_constant == c.name
            assert m.corrected_hex == c.corrected_hex
            assert m.distance == 0.0


def test_nearest_arbitrary_cases():
    # Hand-verified neighbors.
    # Near-black snaps to black.
    assert nearest("#050505").hex == "000000"
    # Mid gray (0x80) is closer to AA than to 55: |128-170|=42 < |128-85|=43.
    assert nearest("#808080").hex == "AAAAAA"
    # A sky blue lands on VividCerulean (00AAFF).
    assert nearest((10, 160, 240)).hex == "00AAFF"
    # Pure-ish amber snaps to ChromeYellow.
    assert nearest("#F5A010").hex == "FFAA00"
    # Off-white snaps to white.
    assert nearest("#FDFDFD").hex == "FFFFFF"


def test_nearest_distance_is_euclidean():
    m = nearest("#050505")  # distance to 000000 = sqrt(3*25)
    assert m.distance == pytest.approx((3 * 25) ** 0.5, abs=1e-3)


def test_nearest_accepts_shorthand_and_tuple():
    # "#0af" expands to 00AAFF, an exact palette entry (VividCerulean).
    m = nearest("#0af")
    assert m.exact is True and m.hex == "00AAFF"
    assert nearest((0, 0, 0)).exact is True


# ---------------------------------------------------------------------------
# parse_color validation
# ---------------------------------------------------------------------------
def test_parse_color_forms():
    assert parse_color("#00AAFF") == (0, 170, 255)
    assert parse_color("00aaff") == (0, 170, 255)
    assert parse_color("#0af") == (0, 170, 255)
    assert parse_color((1, 2, 3)) == (1, 2, 3)
    assert parse_color(PALETTE[0]) == (0, 0, 0)


@pytest.mark.parametrize("bad", ["#12", "gggggg", "12345", (0, 0, 300), (-1, 0, 0)])
def test_parse_color_rejects_bad(bad):
    with pytest.raises(ValueError):
        parse_color(bad)


def test_parse_color_error_names_expected_format():
    with pytest.raises(ValueError, match="RRGGBB"):
        parse_color("12345")
    with pytest.raises(ValueError, match="hexadecimal"):
        parse_color("gggggg")
    with pytest.raises(ValueError, match="0-255"):
        parse_color((0, 0, 300))


# ---------------------------------------------------------------------------
# contrast_ratio() against known WCAG example pairs
# ---------------------------------------------------------------------------
def test_contrast_extremes():
    # Black vs white is the maximum ratio, 21:1.
    assert contrast_ratio("000000", "FFFFFF") == pytest.approx(21.0, abs=0.01)
    # A color against itself is 1:1.
    assert contrast_ratio("00AAFF", "00AAFF") == pytest.approx(1.0, abs=0.001)


def test_contrast_symmetric():
    assert contrast_ratio("123456", "abcdef") == contrast_ratio("abcdef", "123456")


def test_contrast_known_pair():
    # #777777 on #FFFFFF is a well-known WCAG example ~= 4.48:1 (just under AA).
    assert contrast_ratio("777777", "FFFFFF") == pytest.approx(4.48, abs=0.05)
    # #949494 on white ~= 3.0:1 (the AA large-text boundary).
    assert contrast_ratio("949494", "FFFFFF") == pytest.approx(3.0, abs=0.05)


def test_legible_threshold():
    # White on black clears everything.
    assert legible("FFFFFF", "000000") is True
    # DarkGray text on black fails the 4.5 floor (contrast ~2.6).
    assert legible("555555", "000000") is False
    # ...but VividCerulean on black clears the large-text 3.0 bar.
    assert legible("00AAFF", "000000", threshold=LEGIBLE_THRESHOLD_LARGE) is True


# ---------------------------------------------------------------------------
# swatch()
# ---------------------------------------------------------------------------
def _png_size(data: bytes) -> tuple[int, int]:
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    w, h = struct.unpack(">II", data[16:24])  # IHDR width/height
    return w, h


def test_swatch_single_color():
    png = swatch(["00AAFF"])
    w, h = _png_size(png)
    # 1 color -> 1x1 grid, scale 2: (92 + 4) * 2 wide, (56 + 4) * 2 tall.
    assert (w, h) == ((92 + 4) * 2, (40 + 16 + 4) * 2)


def test_swatch_full_palette_grid():
    png = swatch([c.hex for c in PALETTE])
    w, h = _png_size(png)
    # 64 colors -> 8x8 grid at default scale 2.
    assert (w, h) == ((8 * 92 + 4) * 2, (8 * 56 + 4) * 2)


def test_swatch_accepts_mixed_inputs_and_labels():
    png = swatch(
        [PALETTE[0], (255, 0, 0), "#00FF00"],
        labels=["black", "red", "green"],
    )
    assert _png_size(png)  # valid PNG, dimensions computed


def test_swatch_columns_override():
    png = swatch([c.hex for c in PALETTE[:8]], columns=4)
    w, h = _png_size(png)
    assert (w, h) == ((4 * 92 + 4) * 2, (2 * 56 + 4) * 2)


def test_swatch_scale():
    a = _png_size(swatch(["000000"], scale=1))
    b = _png_size(swatch(["000000"], scale=2))
    assert b[0] == 2 * a[0] and b[1] == 2 * a[1]


def test_swatch_pil_loadable():
    from PIL import Image

    img = Image.open(io.BytesIO(swatch(["FF0000", "00FF00", "0000FF"])))
    img.load()
    assert img.mode == "RGB"


def test_swatch_errors():
    # Messages must name the actual MCP tool (palette_swatch), not the
    # internal swatch() function name, and the labels-mismatch message must
    # include the actual counts.
    with pytest.raises(ValueError, match="palette_swatch"):
        swatch([])
    with pytest.raises(ValueError, match=r"1 labels for 2 colors"):
        swatch(["000000", "FFFFFF"], labels=["only-one"])


# ---------------------------------------------------------------------------
# ADVERSARIAL (roadmap 2.3)
# ---------------------------------------------------------------------------
def test_swatch_rejects_more_than_max_colors():
    from pebble_mcp.palette import MAX_SWATCH_COLORS

    # At the cap is fine; over the cap is a clean ValueError, not a giant image.
    swatch(["#000000"] * MAX_SWATCH_COLORS)
    with pytest.raises(ValueError, match="(?i)at most|too many|colors") as exc_info:
        swatch(["#000000"] * (MAX_SWATCH_COLORS + 1))
    assert "palette_swatch" in str(exc_info.value)


def test_swatch_mixed_junk_color_raises_valueerror():
    with pytest.raises(ValueError):
        swatch(["#000000", "notacolor", "#FFFFFF"])


def test_swatch_absurdly_long_label_stays_bounded():
    # A 10k-char label must not blow up the canvas dimensions (it renders
    # clipped within its fixed cell).
    png = swatch(["#000000"], labels=["X" * 10000])
    w, h = _png_size(png)
    assert (w, h) == ((92 + 4) * 2, (40 + 16 + 4) * 2)


@pytest.mark.parametrize("bad", ["", "#GGGGGG", "#12345", (1, 2, 3, 4), (-1, 0, 0)])
def test_nearest_rejects_junk(bad):
    with pytest.raises(ValueError):
        nearest(bad)


# ---------------------------------------------------------------------------
# generator script parity
# ---------------------------------------------------------------------------
def test_generator_reproduces_embedded_table():
    import importlib.util

    script = Path(__file__).resolve().parents[1] / "scripts" / "gen_palette.py"
    spec = importlib.util.spec_from_file_location("gen_palette", script)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    generated = mod.build_table(ASSETS)
    assert generated == palette.PALETTE_DATA
