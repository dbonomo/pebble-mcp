"""Tests for the pebble_mcp.images pipeline (roadmap task B2).

Synthetic images are generated with Pillow on the fly — no fixtures needed.
"""

from __future__ import annotations

import base64
import io

import pytest
from PIL import Image

from pebble_mcp import images, palette
from pebble_mcp.palette import PALETTE

# Set of every valid palette RGB triple (the 64-color gamut).
PALETTE_RGBS = {c.rgb for c in PALETTE}
CORRECTED_RGBS = {palette._hex_to_rgb(c.corrected_hex) for c in PALETTE}


# --------------------------------------------------------------------------- #
# Fixtures / helpers
# --------------------------------------------------------------------------- #
def _gradient(w: int = 24, h: int = 24) -> Image.Image:
    """A smooth RGB gradient — off-palette everywhere, good for dither tests."""
    img = Image.new("RGB", (w, h))
    px = img.load()
    for y in range(h):
        for x in range(w):
            px[x, y] = (int(255 * x / (w - 1)), int(255 * y / (h - 1)), 128)
    return img


def _all_pixels_in(img: Image.Image, allowed: set[tuple[int, int, int]]) -> bool:
    return all(rgb in allowed for _, rgb in (img.getcolors(maxcolors=1 << 16) or []))


# --------------------------------------------------------------------------- #
# Quantize: palette-only output
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("dither", ["floyd-steinberg", "none", "ordered"])
def test_quantize_output_is_palette_only(dither):
    result = images.quantize(_gradient(), dither=dither)
    assert result.image.mode == "RGB"
    assert _all_pixels_in(result.image, PALETTE_RGBS), f"{dither} produced off-palette pixels"


def test_quantize_colors_used_are_palette_names():
    result = images.quantize(_gradient(), dither="none")
    names = {c.name for c in PALETTE}
    assert result.colors_used, "expected some colors"
    for cu in result.colors_used:
        assert cu.name in names
        assert palette.get(cu.hex) is not None
    # counts sum to the pixel count.
    assert sum(c.count for c in result.colors_used) == 24 * 24


def test_quantize_unknown_dither_raises():
    with pytest.raises(ValueError):
        images.quantize(_gradient(), dither="bogus")


# --------------------------------------------------------------------------- #
# Quantize: dither vs none differ
# --------------------------------------------------------------------------- #
def test_dither_vs_none_differ():
    none = images.quantize(_gradient(), dither="none").image
    fs = images.quantize(_gradient(), dither="floyd-steinberg").image
    ordered = images.quantize(_gradient(), dither="ordered").image
    assert none.tobytes() != fs.tobytes()
    assert none.tobytes() != ordered.tobytes()


# --------------------------------------------------------------------------- #
# Quantize: exact palette input maps to itself, zero distance
# --------------------------------------------------------------------------- #
def test_on_palette_image_has_zero_distance():
    # Build an 8×8 image straight from the palette.
    img = Image.new("RGB", (8, 8))
    img.putdata([c.rgb for c in PALETTE])
    result = images.quantize(img, dither="none")
    assert result.max_distance == 0.0
    assert result.mean_distance == 0.0
    assert _all_pixels_in(result.image, PALETTE_RGBS)


# --------------------------------------------------------------------------- #
# Quantize: corrected preview
# --------------------------------------------------------------------------- #
def test_corrected_preview_uses_corrected_values():
    # A solid pure red maps to GColorRed (FF0000) whose corrected hex is E35462.
    red = Image.new("RGB", (4, 4), (255, 0, 0))
    result = images.quantize(red, dither="none", corrected=True)
    assert result.corrected is True
    # Preview pixels are corrected display values, not raw palette entries.
    assert _all_pixels_in(result.image, CORRECTED_RGBS)
    corrected_red = palette._hex_to_rgb(palette.get("FF0000").corrected_hex)
    assert result.image.getpixel((0, 0)) == corrected_red
    # Reporting still names the true palette color.
    assert [c.name for c in result.colors_used] == ["GColorRed"]


def test_uncorrected_preview_is_raw_palette():
    red = Image.new("RGB", (4, 4), (255, 0, 0))
    result = images.quantize(red, dither="none", corrected=False)
    assert result.image.getpixel((0, 0)) == (255, 0, 0)


def test_alpha_is_flattened_onto_black():
    # Fully transparent image -> black after flattening.
    img = Image.new("RGBA", (4, 4), (255, 0, 0, 0))
    result = images.quantize(img, dither="none")
    assert result.image.getpixel((0, 0)) == (0, 0, 0)


# --------------------------------------------------------------------------- #
# Prep: dimensions & letterbox
# --------------------------------------------------------------------------- #
def test_target_dimensions():
    assert images.TARGETS["emery"] == (200, 228)
    assert images.TARGETS["menu-icon"] == (25, 25)
    assert images.TARGETS["appstore-banner"] == (720, 320)


@pytest.mark.parametrize(
    "target,size",
    [("emery", (200, 228)), ("menu-icon", (25, 25)), ("appstore-banner", (720, 320))],
)
def test_prep_output_dimensions(target, size):
    out = images.prep(_gradient(40, 40), target)
    assert out.size == size
    assert _all_pixels_in(out, PALETTE_RGBS)


def test_prep_contain_letterboxes_with_background():
    # A wide source into the tall emery box (contain) leaves black bars top/bottom.
    src = Image.new("RGB", (100, 10), (255, 255, 255))
    out = images.resize_to_target(src, "emery", fit="contain")
    assert out.size == (200, 228)
    # Top-left corner is letterbox (black); vertical center holds content (white).
    assert out.getpixel((0, 0)) == (0, 0, 0)
    assert out.getpixel((100, 114)) == (255, 255, 255)


def test_prep_contain_custom_background():
    src = Image.new("RGB", (100, 10), (255, 255, 255))
    out = images.resize_to_target(src, "emery", fit="contain", background="FF0000")
    assert out.getpixel((0, 0)) == (255, 0, 0)


def test_prep_cover_crops_no_letterbox():
    # Cover fills the whole box: a solid source has no background bars at all.
    src = Image.new("RGB", (100, 10), (255, 255, 255))
    out = images.resize_to_target(src, "emery", fit="cover")
    assert out.size == (200, 228)
    assert _all_pixels_in(out, {(255, 255, 255)})


def test_prep_unknown_target_raises():
    with pytest.raises(ValueError):
        images.resize_to_target(_gradient(), "nope")


def test_prep_unknown_fit_raises():
    with pytest.raises(ValueError):
        images.resize_to_target(_gradient(), "emery", fit="stretch")


def test_platform_targets_present():
    # Platform screen sizes are reused as targets (single source of truth).
    assert images.TARGETS["basalt"] == (144, 168)
    assert images.TARGETS["chalk"] == (180, 180)


# --------------------------------------------------------------------------- #
# preview_2x
# --------------------------------------------------------------------------- #
def test_preview_2x_doubles_and_is_nearest():
    src = Image.new("RGB", (2, 2))
    src.putdata([(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 255)])
    out = images.preview_2x(src)
    assert out.size == (4, 4)
    # Nearest-neighbor: the top-left 2×2 block is all the original top-left pixel.
    assert out.getpixel((0, 0)) == (255, 0, 0)
    assert out.getpixel((1, 1)) == (255, 0, 0)
    assert out.getpixel((2, 0)) == (0, 255, 0)


# --------------------------------------------------------------------------- #
# load_image: path and base64 inputs
# --------------------------------------------------------------------------- #
def _png_bytes(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def test_load_image_from_path(tmp_path):
    p = tmp_path / "src.png"
    _gradient().save(p)
    loaded = images.load_image(str(p))
    assert loaded.size == (24, 24)


def test_load_image_from_data_uri():
    data = base64.b64encode(_png_bytes(_gradient(8, 8))).decode()
    uri = f"data:image/png;base64,{data}"
    loaded = images.load_image(uri)
    assert loaded.size == (8, 8)


def test_load_image_from_bare_base64():
    data = base64.b64encode(_png_bytes(_gradient(8, 8))).decode()
    loaded = images.load_image(data)
    assert loaded.size == (8, 8)


def test_load_image_from_bytes():
    loaded = images.load_image(_png_bytes(_gradient(8, 8)))
    assert loaded.size == (8, 8)


def test_load_image_bad_input_raises():
    with pytest.raises(ValueError):
        images.load_image("not a path and not base64 @@@")


def test_load_image_malformed_data_uri_raises():
    with pytest.raises(ValueError, match="data:image/png;base64,"):
        images.load_image("data:image/png;base64")


def test_load_image_nonexistent_path_message_is_bounded():
    # A long path-looking, nonexistent string must not be echoed unbounded into
    # the error message (see images._ECHO_CAP).
    bogus = "/no/such/" + ("x" * 500) + "/file.png"
    with pytest.raises(ValueError) as exc_info:
        images.load_image(bogus)
    msg = str(exc_info.value)
    assert len(msg) < 300
    assert "more chars" in msg


def test_end_to_end_base64_quantize():
    data = base64.b64encode(_png_bytes(_gradient())).decode()
    uri = f"data:image/png;base64,{data}"
    result = images.quantize(images.load_image(uri), dither="floyd-steinberg")
    assert _all_pixels_in(result.image, PALETTE_RGBS)


def test_to_png_bytes_roundtrips():
    png = images.to_png_bytes(_gradient(8, 8))
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    assert Image.open(io.BytesIO(png)).size == (8, 8)


# --------------------------------------------------------------------------- #
# ADVERSARIAL (roadmap 2.3): malformed / hostile image inputs.
# load_image must always fail with a clean ValueError, never leak a raw PIL
# OSError / UnidentifiedImageError, regardless of which input path was taken.
# --------------------------------------------------------------------------- #
def test_load_image_truncated_bytes_raises_valueerror():
    # A truncated PNG (header only) used to leak PIL's UnidentifiedImageError
    # from the bytes path while the base64 path wrapped it -- inconsistent.
    full = _png_bytes(Image.new("RGB", (32, 32), (255, 0, 0)))
    with pytest.raises(ValueError):
        images.load_image(full[:20])


def test_load_image_corrupt_body_raises_valueerror_not_deferred_crash():
    # Header-valid PNG with a mangled compressed body: lazy Image.open() used to
    # succeed and then crash deep inside quantize(). load_image must catch it.
    full = bytearray(_png_bytes(_gradient(32, 32)))
    # Corrupt the tail (inside IDAT/IEND) so decode fails at load time.
    for i in range(len(full) - 30, len(full) - 4):
        full[i] ^= 0xFF
    with pytest.raises(ValueError):
        img = images.load_image(bytes(full))
        images.quantize(img, dither="none")  # if load didn't catch it, this must not crash raw


def test_load_image_directory_path_raises_valueerror(tmp_path):
    with pytest.raises(ValueError):
        images.load_image(str(tmp_path))


def test_load_image_nonexistent_path_raises_valueerror():
    with pytest.raises(ValueError):
        images.load_image("/no/such/file/anywhere.png")


def test_load_image_base64_of_non_image_raises_valueerror():
    data = base64.b64encode(b"definitely not an image payload, just text").decode()
    with pytest.raises(ValueError):
        images.load_image(data)


def test_load_image_data_uri_wrong_mime_still_loads_valid_png():
    # We decode by payload, not by the declared MIME. A valid PNG behind a
    # text/plain data URI still loads (documented lenient behavior).
    data = base64.b64encode(_png_bytes(_gradient(8, 8))).decode()
    loaded = images.load_image(f"data:text/plain;base64,{data}")
    assert loaded.size == (8, 8)


def test_load_image_rejects_decompression_bomb_dimensions(monkeypatch):
    # A PNG whose header declares an enormous canvas must be rejected by our
    # documented pixel cap before any full decode/allocation happens.
    # Build a real (small) image, then lie about the cap so we don't need to
    # allocate gigapixels in the test.
    monkeypatch.setattr(images, "MAX_IMAGE_PIXELS", 100)
    with pytest.raises(ValueError, match="(?i)too large|pixel|cap"):
        images.load_image(_png_bytes(Image.new("RGB", (64, 64))))


def test_load_image_within_cap_still_loads(monkeypatch):
    monkeypatch.setattr(images, "MAX_IMAGE_PIXELS", 100)
    loaded = images.load_image(_png_bytes(Image.new("RGB", (8, 8))))
    assert loaded.size == (8, 8)


@pytest.mark.parametrize("mode", ["L", "P", "CMYK", "I;16", "1", "LA", "RGBA"])
def test_quantize_accepts_all_pixel_modes(mode):
    # greyscale, palette, CMYK, 16-bit, bilevel, alpha -- all coerce to RGB and
    # quantize to on-palette output without raising.
    img = Image.new(mode, (4, 4))
    result = images.quantize(img, dither="none")
    assert _all_pixels_in(result.image, PALETTE_RGBS)


def test_quantize_extreme_aspect_ratio_prep():
    # 1x1000 sliver into a 25x25 icon must not crash (min-1px scale guard).
    src = Image.new("RGB", (1, 1000), (255, 255, 255))
    out = images.prep(src, "menu-icon")
    assert out.size == (25, 25)
