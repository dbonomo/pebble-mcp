"""Image pipeline for the Tier-2 design toolkit (roadmap task B2).

Pure library (Pillow only, no toolchain/network). Everything here maps ordinary
artwork into the Pebble 64-color world and prepares it for on-watch / appstore
use. The palette itself — the 64 ``GColor`` entries, their sunlight-corrected
display values, and nearest-color matching — lives in
:mod:`pebble_mcp.palette`; this module reuses that table and never duplicates it.

What lives here:

* :func:`quantize` — map any image onto the 64-color palette with a choice of
  dithering (Floyd-Steinberg / none / ordered), optionally previewing in the
  sunlight-corrected display values. Reports which ``GColor`` entries were used
  and how far pixels had to move (per-pixel sRGB distance stats).
* :func:`resize_to_target` / :func:`prep` — fit artwork to a named target
  (emery screen, launcher menu icon, appstore banner, …) with contain-letterbox
  or cover-crop, then quantize.
* :func:`preview_2x` — nearest-neighbor 2× upscale for eyeballing tiny assets.
* :func:`load_image` — accept a filesystem path **or** a base64 / data-URI
  string, so hosts without a filesystem can still send pixels.

## Why quantization is exact here

The palette is the full 2-bit-per-channel RGB cube: every channel is one of
``0x00 / 0x55 / 0xAA / 0xFF``, so all 4×4×4 = 64 combinations are palette
entries (see :mod:`pebble_mcp.palette`). That makes ordered dithering trivially
exact — dithering each channel to its four levels can only ever land on a real
``GColor`` — and lets us build a Pillow palette image for the Floyd-Steinberg /
no-dither paths that is guaranteed to emit palette-only output.

``corrected=True`` remaps the quantized result to the sunlight-corrected display
hexes (``PaletteColor.corrected_hex``) *for preview only* — those corrected
values are not themselves palette entries, so ``colors_used`` and the distance
stats are always computed against the true (uncorrected) palette mapping.

## Target dimensions and their sources

* ``emery`` 200×228 — Pebble Time 2 full screen (status bar off). Verified in
  this repo's DESIGN.md and :mod:`pebble_mcp.platforms` (which the other
  platform targets are pulled from directly, so there is one source of truth).
* ``menu-icon`` 25×25 — Pebble launcher menu icon. Official Rebble/Core Devices
  SDK docs: "add a 25x25 png … icons that are larger will be rejected by the
  SDK" (developer.repebble.com → App Resources → Images).
* ``appstore-banner`` 720×320 — appstore marketing banner shown above the
  screenshots (developer.repebble.com → Appstore Publishing → Appstore Assets;
  the 720×320 PNG figure is the long-standing Pebble appstore banner spec, also
  documented in third-party submission guides).
"""

from __future__ import annotations

import base64
import binascii
import io
import os
from dataclasses import dataclass
from typing import NamedTuple

# Pillow is a headline dependency but treated as optional-at-runtime: importing
# this module must NEVER fail just because Pillow is absent or broken, so the
# server (and every Tier-1/3 tool) keeps working. Callers that actually touch
# pixels are gated at the tool layer (see tools_design._require_pillow), which
# raises a clear "install Pillow" message instead of an ImportError traceback.
# When Pillow is missing, ``Image`` is None and every function here that uses it
# is simply never reached.
try:
    from PIL import Image
except Exception:  # pragma: no cover - exercised via sys.modules stubbing in tests
    Image = None  # type: ignore[assignment,misc]

from . import palette
from .palette import PALETTE
from .platforms import PLATFORMS

# --------------------------------------------------------------------------- #
# Targets
# --------------------------------------------------------------------------- #
# Named prep targets -> (width, height). Per-platform screen sizes come straight
# from the platforms table (single source of truth); the extra targets are the
# launcher icon and appstore banner (sources documented in the module docstring).
_EXTRA_TARGETS: dict[str, tuple[int, int]] = {
    "menu-icon": (25, 25),
    "appstore-banner": (720, 320),
}

TARGETS: dict[str, tuple[int, int]] = {
    **{name: (int(spec["width"]), int(spec["height"])) for name, spec in PLATFORMS.items()},
    **_EXTRA_TARGETS,
}

# Dither mode names accepted by :func:`quantize`.
DITHER_MODES = ("floyd-steinberg", "none", "ordered")

# 4×4 Bayer threshold matrix, normalized to (0, 1). Used for ordered dithering.
_BAYER4 = [
    [0, 8, 2, 10],
    [12, 4, 14, 6],
    [3, 11, 1, 9],
    [15, 7, 13, 5],
]
_BAYER_N = 4
_BAYER_NORM = [[(v + 0.5) / 16.0 for v in row] for row in _BAYER4]

# Per-channel palette levels (the 2-bit cube) and the step between them.
_LEVELS = (0, 85, 170, 255)
_STEP = 85

# Corrected-preview lookup: uncorrected palette RGB -> corrected display RGB.
_CORRECTED_RGB: dict[tuple[int, int, int], tuple[int, int, int]] = {
    c.rgb: palette._hex_to_rgb(c.corrected_hex) for c in PALETTE
}
# Palette RGB -> GColor name, for reporting colors used.
_NAME_BY_RGB: dict[tuple[int, int, int], str] = {c.rgb: c.name for c in PALETTE}


# --------------------------------------------------------------------------- #
# Results
# --------------------------------------------------------------------------- #
class ColorUse(NamedTuple):
    """One palette color that appears in a quantized image, with its pixel count."""

    name: str  # GColor* name
    hex: str  # uncorrected 6-digit hex, no leading '#'
    count: int


@dataclass
class QuantizeResult:
    """Outcome of :func:`quantize`.

    Attributes:
        image: the preview :class:`PIL.Image.Image` (RGB). When ``corrected`` is
            set this is rendered in the sunlight-corrected display values; the
            true palette mapping is still reflected in ``colors_used``/stats.
        colors_used: palette entries present, most-used first.
        mean_distance / max_distance: per-pixel Euclidean sRGB distance between
            the original pixel and the (uncorrected) palette color it mapped to.
            0 means the source was already on-palette.
        dither: the dither mode used.
        corrected: whether ``image`` is the corrected-display preview.
    """

    image: Image.Image
    colors_used: list[ColorUse]
    mean_distance: float
    max_distance: float
    dither: str
    corrected: bool


# --------------------------------------------------------------------------- #
# Input loading (path or base64 / data URI)
# --------------------------------------------------------------------------- #
# Decompression-bomb cap. Any source image whose declared canvas exceeds this
# many pixels is rejected *before* the pixel data is decoded, so a tiny hostile
# file that claims gigantic dimensions can't force a multi-GB allocation. The
# limit is deliberately generous for this domain — the largest thing this
# toolkit ever produces is the 720×320 appstore banner (~0.23 MP) and even a
# 4K source (~8.3 MP) fits comfortably — while staying far under Pillow's own
# ~89 MP ``Image.MAX_IMAGE_PIXELS`` warning threshold. Callers who genuinely
# need larger inputs can raise ``images.MAX_IMAGE_PIXELS`` at runtime.
MAX_IMAGE_PIXELS = 40_000_000  # 40 megapixels


_ECHO_CAP = 80


def _bounded_repr(text: str) -> str:
    """``repr()`` of ``text``, truncated to :data:`_ECHO_CAP` chars for safe
    echoing in error messages (never reflect an unbounded caller-supplied
    string back verbatim)."""
    if len(text) <= _ECHO_CAP:
        return repr(text)
    return repr(text[:_ECHO_CAP]) + f"...(+{len(text) - _ECHO_CAP} more chars)"


def load_image(source: str | bytes) -> Image.Image:
    """Load an image from a filesystem path, a base64 string, or a data URI.

    Hosts without a filesystem (claude.ai, remote connectors) send pixels as a
    ``data:image/png;base64,...`` URI or bare base64; local hosts send a path.
    Raw ``bytes`` are also accepted (already-decoded image data). The declared
    MIME type of a ``data:`` URI is ignored — decoding is by content, so a valid
    image behind a mislabeled MIME still loads.

    The returned image is fully decoded and validated here (not lazily), so a
    truncated or corrupt source fails at this call rather than crashing deep in
    a later pipeline stage. Images larger than :data:`MAX_IMAGE_PIXELS` are
    rejected up front (decompression-bomb guard).

    Raises:
        ValueError: if the source is not a readable file / decodable base64
            image, is truncated/corrupt, or exceeds the pixel cap. Never leaks
            a raw Pillow ``OSError`` / ``UnidentifiedImageError``.
    """
    if isinstance(source, bytes):
        return _open_verified(io.BytesIO(source), "image bytes")

    if not isinstance(source, str):  # pragma: no cover - defensive
        raise ValueError(f"unsupported image source type: {type(source).__name__}")

    text = source.strip()

    # data URI: strip the "data:...;base64," prefix.
    if text.startswith("data:"):
        comma = text.find(",")
        if comma == -1:
            raise ValueError(
                "malformed data URI: no comma before the payload; expected the form "
                "'data:image/png;base64,<payload>'"
            )
        payload = text[comma + 1 :]
        return _decode_b64_image(payload)

    # A short-enough string that names an existing file: open it.
    if len(text) < 4096 and os.path.isfile(text):
        return _open_verified(text, f"file {_bounded_repr(text)}")

    # A path-looking string that isn't an existing file: don't silently treat it
    # as base64 (produces a confusing "not valid base64" error for a typo/dir).
    if len(text) < 4096 and (os.path.sep in text or os.path.exists(text)):
        raise ValueError(
            f"{_bounded_repr(text)} is not a readable image file; check the path exists, "
            "or pass base64 / a 'data:' URI instead of a path"
        )

    # Otherwise assume bare base64.
    return _decode_b64_image(text)


def _decode_b64_image(payload: str) -> Image.Image:
    cleaned = "".join(payload.split())
    try:
        raw = base64.b64decode(cleaned, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError(
            "image source is not a readable file path or valid base64 data"
        ) from exc
    return _open_verified(io.BytesIO(raw), "base64 payload")


def _open_verified(fp: str | io.BytesIO, desc: str) -> Image.Image:
    """Open ``fp``, enforce the pixel cap on the declared size, and fully decode.

    Any failure — unidentifiable header, truncated/corrupt body, or an
    over-cap canvas — is normalized to a ``ValueError`` so callers get one
    consistent, actionable error type regardless of the input path taken.
    """
    try:
        img = Image.open(fp)
    except (OSError, ValueError, Image.DecompressionBombError) as exc:
        # UnidentifiedImageError is an OSError subclass; catch it here too.
        raise ValueError(f"{desc} could not be read as an image") from exc

    w, h = img.size
    if w * h > MAX_IMAGE_PIXELS:
        raise ValueError(
            f"{desc} is too large: {w}x{h} = {w * h} pixels exceeds the "
            f"{MAX_IMAGE_PIXELS}-pixel safety cap; downscale the source image before retrying"
        )

    # Force the decode now so truncated/corrupt data fails here, not later.
    try:
        img.load()
    except (OSError, Image.DecompressionBombError) as exc:
        raise ValueError(f"{desc} is a truncated or corrupt image") from exc
    return img


# --------------------------------------------------------------------------- #
# Quantization
# --------------------------------------------------------------------------- #
def _palette_image() -> Image.Image:
    """A 1×1 Pillow ``P`` image whose palette is exactly the 64 GColors.

    Used as the target palette for Pillow's dithered/undithered ``quantize``, so
    those code paths are guaranteed to emit only palette colors.
    """
    flat: list[int] = []
    for c in PALETTE:
        flat.extend(c.rgb)
    flat.extend([0] * (768 - len(flat)))  # pad to 256 entries
    pal = Image.new("P", (1, 1))
    pal.putpalette(flat)
    return pal


def _quantize_ordered(rgb: Image.Image) -> Image.Image:
    """Per-channel 4×4 ordered (Bayer) dither onto the 2-bit palette cube.

    Because every (level, level, level) triple is a palette entry, dithering
    each channel independently to its four levels always yields a real GColor.
    """
    w, h = rgb.size
    src = rgb.load()
    out = Image.new("RGB", (w, h))
    dst = out.load()
    for y in range(h):
        trow = _BAYER_NORM[y % _BAYER_N]
        for x in range(w):
            thr = trow[x % _BAYER_N]
            px = src[x, y]
            dst[x, y] = (
                _dither_channel(px[0], thr),
                _dither_channel(px[1], thr),
                _dither_channel(px[2], thr),
            )
    return out


def _dither_channel(v: int, threshold: float) -> int:
    """Map one 0..255 channel value to a palette level using an ordered threshold."""
    base = v // _STEP  # 0..3 (v==255 -> 3)
    if base >= 3:
        return 255
    frac = (v - base * _STEP) / _STEP
    return _LEVELS[base + 1] if frac > threshold else _LEVELS[base]


def _quantize_pillow(rgb: Image.Image, dither: str) -> Image.Image:
    from PIL.Image import Dither

    mode = Dither.FLOYDSTEINBERG if dither == "floyd-steinberg" else Dither.NONE
    quant_p = rgb.quantize(palette=_palette_image(), dither=mode)
    return quant_p.convert("RGB")


def quantize(
    img: Image.Image,
    *,
    dither: str = "floyd-steinberg",
    corrected: bool = False,
) -> QuantizeResult:
    """Map ``img`` onto the Pebble 64-color palette.

    Args:
        img: any PIL image (converted to RGB internally; alpha is flattened onto
            black, matching the watch's opaque framebuffer).
        dither: ``"floyd-steinberg"`` (default), ``"none"``, or ``"ordered"``
            (a 4×4 Bayer pattern). Dithered modes trade flat banding for noise.
        corrected: when True, the returned ``image`` is rendered in the
            sunlight-corrected display values (how the reflective LCD actually
            looks) instead of the bright uncorrected palette hexes. Reporting is
            unaffected — it always reflects the true palette mapping.

    Returns:
        A :class:`QuantizeResult` with the preview image, colors used, and
        per-pixel distance stats.
    """
    if dither not in DITHER_MODES:
        raise ValueError(f"unknown dither {dither!r}; expected one of {DITHER_MODES}")

    rgb = _flatten_to_rgb(img)

    if dither == "ordered":
        quant = _quantize_ordered(rgb)
    else:
        quant = _quantize_pillow(rgb, dither)

    colors_used = _count_colors(quant)
    mean_d, max_d = _distance_stats(rgb, quant)

    preview = _to_corrected(quant) if corrected else quant
    return QuantizeResult(
        image=preview,
        colors_used=colors_used,
        mean_distance=round(mean_d, 3),
        max_distance=round(max_d, 3),
        dither=dither,
        corrected=corrected,
    )


def _flatten_to_rgb(img: Image.Image) -> Image.Image:
    """Convert any mode to RGB, compositing transparency onto black."""
    if img.mode == "RGB":
        return img
    if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
        rgba = img.convert("RGBA")
        bg = Image.new("RGBA", rgba.size, (0, 0, 0, 255))
        return Image.alpha_composite(bg, rgba).convert("RGB")
    return img.convert("RGB")


def _count_colors(quant_rgb: Image.Image) -> list[ColorUse]:
    counts = quant_rgb.getcolors(maxcolors=64) or []
    used: list[ColorUse] = []
    for count, rgb in counts:
        name = _NAME_BY_RGB.get(rgb)
        if name is None:  # pragma: no cover - _palette_image guarantees on-palette
            continue
        used.append(ColorUse(name=name, hex=palette.rgb_to_hex(rgb), count=count))
    used.sort(key=lambda c: (-c.count, c.name))
    return used


def _distance_stats(orig_rgb: Image.Image, quant_rgb: Image.Image) -> tuple[float, float]:
    total = 0.0
    worst = 0.0
    n = 0
    a = orig_rgb.tobytes()
    b = quant_rgb.tobytes()
    for i in range(0, len(a), 3):
        d = (
            (a[i] - b[i]) ** 2 + (a[i + 1] - b[i + 1]) ** 2 + (a[i + 2] - b[i + 2]) ** 2
        ) ** 0.5
        total += d
        if d > worst:
            worst = d
        n += 1
    return (total / n if n else 0.0), worst


def _to_corrected(quant_rgb: Image.Image) -> Image.Image:
    raw = quant_rgb.tobytes()
    out = Image.new("RGB", quant_rgb.size)
    out.putdata(
        [_CORRECTED_RGB[(raw[i], raw[i + 1], raw[i + 2])] for i in range(0, len(raw), 3)]
    )
    return out


# --------------------------------------------------------------------------- #
# Prep (resize / letterbox to a named target)
# --------------------------------------------------------------------------- #
def target_size(target: str) -> tuple[int, int]:
    """``(width, height)`` for a named prep target, or raise for unknown names."""
    try:
        return TARGETS[target]
    except KeyError:
        raise ValueError(
            f"unknown target {target!r}; expected one of {sorted(TARGETS)}"
        ) from None


def resize_to_target(
    img: Image.Image,
    target: str,
    *,
    fit: str = "contain",
    background: str | tuple[int, int, int] = (0, 0, 0),
) -> Image.Image:
    """Resize ``img`` to a named ``target`` (RGB, not yet quantized).

    Args:
        target: a key of :data:`TARGETS` (``emery``, ``menu-icon``,
            ``appstore-banner``, or any platform name).
        fit: ``"contain"`` scales to fit inside the box and letterboxes the
            remainder with ``background``; ``"cover"`` scales to fill the box and
            center-crops the overflow. Aspect ratio is always preserved.
        background: letterbox fill for ``contain`` (hex string or RGB tuple);
            defaults to black (a palette color).
    """
    if fit not in ("contain", "cover"):
        raise ValueError(f"unknown fit {fit!r}; expected 'contain' or 'cover'")

    tw, th = target_size(target)
    rgb = _flatten_to_rgb(img)
    sw, sh = rgb.size
    if sw == 0 or sh == 0:  # pragma: no cover - PIL won't produce zero-size images
        raise ValueError("source image has a zero dimension")

    scale = (min if fit == "contain" else max)(tw / sw, th / sh)
    nw, nh = max(1, round(sw * scale)), max(1, round(sh * scale))
    resized = rgb.resize((nw, nh), Image.Resampling.LANCZOS)

    if fit == "cover":
        left, top = (nw - tw) // 2, (nh - th) // 2
        return resized.crop((left, top, left + tw, top + th))

    bg = palette.parse_color(background)
    canvas = Image.new("RGB", (tw, th), bg)
    canvas.paste(resized, ((tw - nw) // 2, (th - nh) // 2))
    return canvas


def prep(
    img: Image.Image,
    target: str,
    *,
    fit: str = "contain",
    background: str | tuple[int, int, int] = (0, 0, 0),
    dither: str = "floyd-steinberg",
    corrected: bool = False,
) -> Image.Image:
    """Resize/letterbox ``img`` to ``target`` and quantize it to the palette.

    Convenience wrapper: :func:`resize_to_target` followed by :func:`quantize`.
    Returns the quantized preview image; use :func:`quantize` directly when you
    also want the colors-used / distance stats.
    """
    resized = resize_to_target(img, target, fit=fit, background=background)
    return quantize(resized, dither=dither, corrected=corrected).image


# --------------------------------------------------------------------------- #
# Preview
# --------------------------------------------------------------------------- #
def preview_2x(img: Image.Image) -> Image.Image:
    """Nearest-neighbor 2× upscale (crisp, pixel-doubled) for eyeballing assets."""
    w, h = img.size
    return img.resize((w * 2, h * 2), Image.Resampling.NEAREST)


# --------------------------------------------------------------------------- #
# Encoding helper
# --------------------------------------------------------------------------- #
def to_png_bytes(img: Image.Image) -> bytes:
    """Encode a PIL image to PNG bytes."""
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
