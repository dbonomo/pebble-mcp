"""Tier-2 "design toolkit" MCP tools (roadmap task B2).

Thin wrappers over the pure libraries :mod:`pebble_mcp.images` and
:mod:`pebble_mcp.palette`. All the real work — quantization, resizing, palette
math, swatch rendering — lives there and is unit-tested there; this module only
adapts those functions to the MCP tool surface (string/dict in, MCP content
out).

Call :func:`register` with the :class:`FastMCP` app to add the tools:

    from pebble_mcp import tools_design
    tools_design.register(mcp)

## Returning an image *and* stats from one tool (SDK 1.28)

``image_quantize`` / ``image_prep`` need to hand back both a preview image and a
stats blob. In the installed ``mcp`` 1.28.1, FastMCP's result converter
(``func_metadata._convert_to_content``) flattens a returned ``list``/``tuple``
by converting each element and chaining the results: an
:class:`mcp.server.fastmcp.Image` becomes an ``ImageContent`` block and a
``dict`` becomes a JSON ``TextContent`` block. So returning ``[stats_dict,
Image(...)]`` yields a two-block tool result — stats text plus the rendered
preview — which is exactly what we want.

The one catch: if a tool declares a structured return annotation, FastMCP builds
an output schema and tries to validate the return against it, which a mixed
list is not. We therefore register those two tools with
``structured_output=False`` so only the unstructured (content-block) path runs.
Verified against mcp 1.28.1.
"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP, Image
from mcp.server.fastmcp.exceptions import ToolError

from . import images, palette
from .capabilities import pillow_available

_MAX_PREVIEW_UPSCALE = 512  # don't 2×-upscale anything already this wide/tall


def _require_pillow() -> None:
    """Gate the pixel-pushing design tools on Pillow being importable.

    Pillow is a declared dependency, but if the environment has it absent or
    broken we degrade with an actionable message at tool-call time rather than
    leaking an ImportError traceback (the module imports fine regardless — see
    ``pebble_mcp.images``). Pure-math tools (``color_nearest``) do not call this
    and keep working without Pillow.
    """
    if not pillow_available():
        raise ToolError(
            "this design tool needs Pillow, which is not available in this "
            "environment. Install it with `uv pip install pillow` (or "
            "`pip install pillow`) and restart the server. Pure-Python tools "
            "like color_nearest and font_plan work without it; check "
            "capabilities() → tier2_design to see whether the design toolkit "
            "is live."
        )


def register(mcp: FastMCP) -> None:
    """Register the Tier-2 design tools on ``mcp``.

    Adds: ``image_quantize``, ``image_prep`` (image pipeline) and
    ``color_nearest``, ``palette_swatch`` (palette helpers).
    """

    @mcp.tool(structured_output=False)
    def image_quantize(
        source: str,
        dither: str = "floyd-steinberg",
        corrected: bool = False,
    ) -> list[Any]:
        """Quantize an image to the Pebble 64-color palette; return stats + preview.

        Args:
            source: a filesystem path, a base64 string, or a ``data:`` URI of the
                source image (hosts without a filesystem send base64).
            dither: ``"floyd-steinberg"`` (default), ``"none"``, or ``"ordered"``.
            corrected: preview in the sunlight-corrected display values instead of
                the bright uncorrected palette (reporting is unaffected).

        Returns a stats object (colors used, per-pixel distance) plus the
        quantized preview image (2× nearest-neighbor upscale when small).
        """
        _require_pillow()
        img = images.load_image(source)
        result = images.quantize(img, dither=dither, corrected=corrected)
        stats = _quantize_stats(result)
        return [stats, _preview_image(result.image)]

    @mcp.tool(structured_output=False)
    def image_prep(
        source: str,
        target: str = "emery",
        fit: str = "contain",
        dither: str = "floyd-steinberg",
        corrected: bool = False,
    ) -> list[Any]:
        """Resize/letterbox an image to a named target, quantize, and preview it.

        Args:
            source: filesystem path, base64, or ``data:`` URI of the source image.
            target: ``"emery"`` (200×228), ``"menu-icon"`` (25×25),
                ``"appstore-banner"`` (720×320), or any platform name.
            fit: ``"contain"`` (letterbox) or ``"cover"`` (center-crop).
            dither: ``"floyd-steinberg"`` (default), ``"none"``, or ``"ordered"``.
            corrected: preview in sunlight-corrected display values.

        Returns a stats object (target size, colors used, distance) plus the
        prepared preview image (2× nearest-neighbor upscale when small).
        """
        _require_pillow()
        img = images.load_image(source)
        resized = images.resize_to_target(img, target, fit=fit)
        result = images.quantize(resized, dither=dither, corrected=corrected)
        stats = _quantize_stats(result)
        tw, th = images.target_size(target)
        stats["target"] = target
        stats["target_size"] = {"width": tw, "height": th}
        stats["fit"] = fit
        return [stats, _preview_image(result.image)]

    @mcp.tool()
    def color_nearest(color: str, background: str | None = None) -> dict[str, Any]:
        """Nearest of the 64 Pebble GColors to an arbitrary color.

        Args:
            color: a hex string (``"#RRGGBB"`` / ``"RRGGBB"`` / shorthand) or an
                ``"r,g,b"`` triple.
            background: optional background color; when given, the contrast ratio
                and legibility of the matched color on it are also reported.

        Returns the matched GColor name, hex, C constant, packed ARGB8 byte,
        sunlight-corrected display hex, and sRGB distance to the input.
        """
        m = palette.nearest(_parse_color_arg(color))
        entry = palette.get(m.hex)
        out: dict[str, Any] = {
            "exact": m.exact,
            "name": m.name,
            "hex": m.hex,
            "c_constant": m.c_constant,
            "argb8": entry.argb8 if entry else None,
            "corrected_hex": m.corrected_hex,
            "distance": m.distance,
        }
        if background is not None:
            bg = _parse_color_arg(background)
            out["background"] = palette.rgb_to_hex(palette.parse_color(bg))
            out["contrast_ratio"] = palette.contrast_ratio(m.hex, bg)
            out["legible"] = palette.legible(m.hex, bg)
        return out

    @mcp.tool(structured_output=False)
    def palette_swatch(colors: list[str], labels: list[str] | None = None) -> Image:
        """Render a labeled grid of color swatches to a PNG for eyeballing.

        Args:
            colors: hex strings and/or ``"r,g,b"`` triples (1..64 of them).
            labels: optional captions, one per color; defaults to each color's
                palette name (or its hex when off-palette).
        """
        _require_pillow()
        parsed = [_parse_color_arg(c) for c in colors]
        png = palette.swatch(parsed, labels=labels)
        return Image(data=png, format="png")


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _parse_color_arg(color: str) -> str | tuple[int, int, int]:
    """Accept a hex string or an ``"r,g,b"`` triple from a tool argument."""
    if isinstance(color, str) and "," in color:
        parts = [p.strip() for p in color.split(",")]
        if len(parts) == 3:
            try:
                return (int(parts[0]), int(parts[1]), int(parts[2]))
            except ValueError:
                pass
    return color


def _quantize_stats(result: images.QuantizeResult) -> dict[str, Any]:
    return {
        "dither": result.dither,
        "corrected": result.corrected,
        "colors_used": [
            {"name": c.name, "hex": c.hex, "count": c.count} for c in result.colors_used
        ],
        "num_colors": len(result.colors_used),
        "mean_distance": result.mean_distance,
        "max_distance": result.max_distance,
        "preview_size": {"width": result.image.width, "height": result.image.height},
    }


def _preview_image(img: images.Image.Image) -> Image:
    """PNG-encode a preview, 2×-upscaling small images for visibility."""
    if max(img.size) < _MAX_PREVIEW_UPSCALE:
        img = images.preview_2x(img)
    return Image(data=images.to_png_bytes(img), format="png")
