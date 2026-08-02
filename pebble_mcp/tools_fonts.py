"""MCP tool registrations for the Tier 2 font/PDC design toolkit.

Wraps :mod:`pebble_mcp.fonts` (system font recommendations + minimal
``characterRegex``) and :mod:`pebble_mcp.pdc` (SVG -> PDC conversion) as
FastMCP tools. Exposes ``register(mcp)`` per the pattern described in
roadmap.md B3/B4 so ``server.py`` can wire this module in without this file
touching ``server.py`` itself.
"""

from __future__ import annotations

import base64

from mcp.server.fastmcp import FastMCP

from pebble_mcp import fonts as _fonts
from pebble_mcp import pdc as _pdc


def register(mcp: FastMCP) -> None:
    """Register the font-planning and PDC-conversion tools on ``mcp``."""

    @mcp.tool()
    def font_plan(
        role_or_text: str,
        size_hint: int | None = None,
        style: str | None = None,
    ) -> dict:
        """Recommend Pebble system fonts for a UI role or a literal string.

        Pass a role like ``"hero numerals"``, ``"body label"``, ``"title"``,
        ``"timer"``, ``"clock face numerals"``, ``"hint"``, or
        ``"button label"`` (curated from this project's DESIGN.md type
        scale) to get an ordered list of ``FONT_KEY_*`` candidates -- no
        resource cost, they're baked into firmware.

        Pass literal text instead (e.g. ``"15:37"`` or ``"Rest 90s"``) to
        check it against every system font's glyph coverage: numbers-only
        fonts (the LECO family, and the Bitham/Roboto "_NUMBERS"/"_SUBSET"
        variants) that can't render the text are flagged with
        ``fits: false`` and ``missing_glyphs`` instead of being silently
        recommended. Either way, the response includes
        ``minimal_character_regex`` -- the tight ``characterRegex`` for a
        *custom* TTF font that ships only the glyphs actually used (the
        classic "[0-9:]" clock-digit trick from
        developer.repebble.com/guides/app-resources/fonts/).

        ``size_hint`` (pixels) sorts candidates by closeness to that size.
        ``style`` filters/prefers a weight substring, e.g. ``"bold"``.

        Zero-argument sharp edge: this never touches the network or a
        toolchain -- it's pure lookup, always available regardless of
        capability tier.
        """
        return _fonts.font_plan(role_or_text, size_hint=size_hint, style=style)

    @mcp.tool()
    def pdc_convert(svg_text: str) -> dict:
        """Convert an SVG icon/vector to a Pebble Draw Command (.pdc) file.

        Validates PDC's known constraints *before* attempting conversion, so
        a bad SVG comes back as a structured violation list -- never a
        broken or silently-wrong .pdc. Supported SVG elements: g, layer,
        path (straight-line commands M/L/H/V/Z only -- flatten curves
        first), rect, polyline, polygon, line, circle. Not supported:
        gradients, masks, clip paths, filters, embedded text/images, and any
        transform other than translate(x, y).

        On success, returns the .pdc file as base64 (``pdc_base64``) plus
        ``size_bytes``, image ``width``/``height``, ``num_commands``, and
        any non-fatal ``warnings`` (e.g. odd coordinates -- PDC recommends
        an even-integer coordinate grid for crisp rendering). On failure,
        ``valid`` is false, ``pdc_base64`` is absent, and ``violations``
        lists exactly what to fix, one entry per offending element/attribute.

        Next move on failure: fix the listed elements/attributes in the SVG
        (flatten curves to polylines, remove gradients/masks, replace text
        with outlined paths) and call again -- this tool never guesses at a
        lossy conversion.
        """
        result = _pdc.pdc_convert(svg_text)
        out: dict = {
            "valid": result.valid,
            "violations": result.violations,
            "warnings": result.warnings,
        }
        if result.valid:
            assert result.pdc_bytes is not None
            out["pdc_base64"] = base64.b64encode(result.pdc_bytes).decode("ascii")
            out["size_bytes"] = result.size_bytes
            out["width"] = result.width
            out["height"] = result.height
            out["num_commands"] = result.num_commands
        return out
