"""Read-only MCP resources: reference data an agent can pull without a tool call.

Registers four ``pebble://`` resources, each returning a JSON-encoded string
with a stable top-level shape:

* ``pebble://platforms`` -- per-platform display specs (from :mod:`platforms`).
* ``pebble://colors`` -- the 64-color palette table + our house role guidance.
* ``pebble://fonts`` -- system font keys/sizes/usage, distilled from DESIGN.md.
* ``pebble://wire-conventions`` -- the reusable delimited wire-string pattern.

All four are pure functions of in-repo data (platforms.py, palette.py,
DESIGN.md) -- no I/O, no toolchain, always available regardless of capability
tier.
"""

from __future__ import annotations

import json

from mcp.server.fastmcp import FastMCP

from pebble_mcp.palette import PALETTE
from pebble_mcp.platforms import platforms_resource

# ---------------------------------------------------------------------------
# pebble://colors
# ---------------------------------------------------------------------------
# Role guidance table, transcribed from DESIGN.md ("Our current palette (keep
# it small and consistent)"). Roles are named generically (bg/text/accent/
# good/attention/reserved) per the requirements doc's resource shape, with
# the two DESIGN.md text rows (primary/secondary) kept distinct since they
# carry different contrast obligations.
COLOR_ROLES: list[dict[str, str]] = [
    {
        "role": "background",
        "gcolor": "GColorBlack",
        "hex": "000000",
        "usage": "everywhere -- black background is the e-paper sweet spot",
    },
    {
        "role": "text_primary",
        "gcolor": "GColorWhite",
        "hex": "FFFFFF",
        "usage": "values, titles",
    },
    {
        "role": "text_secondary",
        "gcolor": "GColorLightGray",
        "hex": "AAAAAA",
        "usage": "hints, labels, stale markers",
    },
    {
        "role": "accent",
        "gcolor": "GColorVividCerulean",
        "hex": "00AAFF",
        "usage": "brand/identity -- headers, session chip, set counter",
    },
    {
        "role": "good",
        "gcolor": "GColorGreen",
        "hex": "00FF00",
        "usage": "on-pace / go -- protein on-pace, running timer, Send button",
    },
    {
        "role": "attention",
        "gcolor": "GColorChromeYellow",
        "hex": "FFAA00",
        "usage": "behind / needs-focus -- protein behind, reps focus, bonus time",
    },
    {
        "role": "success_flash",
        "gcolor": "GColorMalachite",
        "hex": "00FF55",
        "usage": (
            "logger confirmations. Note: the SDK name for 00FF55 is Malachite "
            "(not MediumSpringGreen, which is 00FFAA) -- trust palette.py over "
            "older docs that mislabel this."
        ),
    },
    {
        "role": "chrome",
        "gcolor": "GColorDarkGray",
        "hex": "555555",
        "usage": "separators, bar troughs -- mid-tones are for chrome only, never text fills",
    },
    {
        "role": "reserved",
        "gcolor": "GColorRed",
        "hex": "FF0000",
        "usage": (
            "genuinely-wrong states only (failed send, protein floor missed at "
            "day's end) -- currently unused elsewhere so it keeps its alarm value"
        ),
    },
]


def colors_resource() -> dict[str, object]:
    """Return the pebble://colors payload: the 64-color table + role guidance."""
    return {
        "colors": [
            {
                "name": c.name,
                "hex": c.hex,
                "corrected_hex": c.corrected_hex,
                "argb8": c.argb8,
            }
            for c in PALETTE
        ],
        "roles": COLOR_ROLES,
    }


# ---------------------------------------------------------------------------
# pebble://fonts — sourced from pebble_mcp.fonts, the authoritative SDK table
# ---------------------------------------------------------------------------


def fonts_resource() -> dict[str, object]:
    """Return the pebble://fonts payload: the full system font table + guidance."""
    from pebble_mcp.fonts import SYSTEM_FONTS

    return {
        "fonts": [
            {
                "key": f.key.removeprefix("FONT_KEY_"),
                "c_key": f.key,
                "family": f.family,
                "points": f.size,
                "weight": f.weight,
                "numbers_only": f.numbers_only,
                "emery_only": f.min_platform == "emery",
                "note": f.note,
            }
            for f in SYSTEM_FONTS
        ],
        "guidance": [
            "One hero value per screen, set in a LECO *_NUMBERS font.",
            "Numbers-only fonts (LECO/Bitham *_NUMBERS, Roboto subset) cannot "
            "render arbitrary text -- never route labels through them; use the "
            "font_plan tool to check glyph fit.",
            "Gothic scale runs GOTHIC_28_BOLD down to GOTHIC_14; GOTHIC_14 is the "
            "floor -- nothing smaller in our apps.",
            "Hints/labels live in the bottom ~40px of a screen, in the secondary "
            "text color (see pebble://colors role text_secondary).",
        ],
    }


# ---------------------------------------------------------------------------
# pebble://wire-conventions
# ---------------------------------------------------------------------------
def wire_conventions_resource() -> dict[str, object]:
    """Return the pebble://wire-conventions payload: the reusable delimited
    wire-string pattern used to move structured data from a phone companion
    (pkjs) to a Pebble C watchapp over a single AppMessage string.

    Generic write-up (no app-specific details) -- the illustrative example
    below is a stand-in shape, not a real project's wire format.
    """
    return {
        "overview": (
            "Pebble AppMessage keys carry typed values, but structured/variable "
            "-length data (a list of rows, several sections) is far simpler to "
            "move as ONE delimited string under ONE messageKey than as many "
            "typed keys. The companion (pkjs) fetches/reduces data into this "
            "string; the watch C code tokenizes it with strtok-style parsing. "
            "One engine, no per-field key bookkeeping, and the shape is easy to "
            "extend by appending a field or section."
        ),
        "delimiters": {
            "section": "~",
            "row": "|",
            "field": "^",
            "rationale": (
                "Three delimiter levels nest cleanly: sections separate logical "
                "groups (e.g. a header vs a list), rows separate repeated "
                "records within a section, fields separate a record's values. "
                "Pick characters that will never appear in real data (or "
                "sanitize them out -- see below)."
            ),
        },
        "example": {
            "description": (
                "Illustrative only -- a generic 'status + items' wire, not a "
                "real project's format."
            ),
            "layout": "status ~ items",
            "status": "level ^ label ^ age_min      level = G|Y|O|R",
            "items": "name ^ value ^ unit   | ... (repeated rows)",
            "sample": "G^All clear^4~Widget A^12^ct|Widget B^7^ct",
        },
        "sanitization": (
            "Any free text field (labels, names, notes) is sanitized before it "
            "goes into the wire string: strip/replace the delimiter characters "
            "themselves (and newlines) so a value can never accidentally start "
            "a new row, section, or field. Do this once, at the point the "
            "string is assembled in pkjs -- the watch-side parser should never "
            "have to defend against a malformed record."
        ),
        "integer_encoding": {
            "principle": (
                "Send integers, never floats -- the watch-side C parser "
                "(atoi/strtol) is simple and exact; float formatting/parsing "
                "across the JS<->C boundary is a source of drift and wasted "
                "code. When the true value has a fractional part, scale it to "
                "an integer and document the scale factor in the wire's header "
                "comment."
            ),
            "examples": [
                "A half-pound-resolution weight: send weight*2 as an int "
                "(e.g. 141 lb -> 282), watch divides by 2 to display.",
                "A tenths-of-an-inch snow total: send inches*10 as an int "
                "(e.g. 4.2in -> 42), watch divides by 10 to display.",
            ],
        },
        "staleness": {
            "pattern": "age_min",
            "description": (
                "Rather than sending an absolute timestamp (which requires "
                "watch and phone clocks to agree, and drifts if they don't), "
                "send age_min -- minutes elapsed between the source data's "
                "last-updated time and the moment pkjs sends the message. The "
                "watch then adds its own elapsed time since receipt to keep the "
                "displayed age current. This makes staleness display immune to "
                "clock skew between devices."
            ),
        },
        "message_key": (
            "Use exactly one AppMessage key (e.g. a single STATE_WIRE-style "
            "string key) for the whole payload rather than one key per field. "
            "This keeps the C-side dictionary small, avoids key-registration "
            "sprawl, and means adding a field is a wire-format change, not an "
            "AppMessage schema change."
        ),
        "bounded_copies": (
            "On the watch, copy the incoming string into a fixed-size stack or "
            "static buffer sized generously for the worst-case wire (not "
            "malloc'd per message), and treat overlength input defensively "
            "(truncate, don't overrun). Cap the number of rows a section can "
            "produce (both companion- and watch-side) so a pathological feed "
            "can't blow past the ~1-2KB practical AppMessage inbox budget or a "
            "fixed-size row array on the watch."
        ),
    }


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------
def register(mcp: FastMCP) -> None:
    """Register all pebble:// read-only resources on the given FastMCP server."""

    @mcp.resource("pebble://platforms")
    def platforms() -> str:
        """Per-platform Pebble display specs (emery, basalt, chalk, diorite)."""
        return json.dumps(platforms_resource(), indent=2)

    @mcp.resource("pebble://colors")
    def colors() -> str:
        """The 64-color Pebble palette table plus our house role guidance."""
        return json.dumps(colors_resource(), indent=2)

    @mcp.resource("pebble://fonts")
    def fonts() -> str:
        """System font keys/sizes/usage guidance distilled from DESIGN.md."""
        return json.dumps(fonts_resource(), indent=2)

    @mcp.resource("pebble://wire-conventions")
    def wire_conventions() -> str:
        """The reusable delimited wire-string pattern for a Pebble companion protocol."""
        return json.dumps(wire_conventions_resource(), indent=2)
