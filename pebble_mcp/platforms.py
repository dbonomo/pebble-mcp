"""Per-platform Pebble display specs.

All seven platforms the active Pebble SDK (v4.17 / Core Devices) can build
for. Width/height/shape/color/touch are transcribed from the SDK's own
authoritative platform table
(``sdk-core/pebble/common/tools/pebble_sdk_platform.py`` -- the
``PBL_DISPLAY_WIDTH`` / ``PBL_DISPLAY_HEIGHT`` / ``PBL_COLOR`` / ``PBL_BW`` /
``PBL_ROUND`` / ``PBL_RECT`` / ``PBL_TOUCH`` defines), so they match exactly
what the compiler bakes into each build.

Two round platforms exist: **chalk** is the original Pebble Time Round
(180x180), and **gabbro** is the *Round 2* platform (Pebble Round 2,
260x260, color + touch). **emery** is the Pebble Time 2 (200x228 rect,
color + touch). **flint** is a new black-and-white rectangular platform
(144x168) added alongside gabbro in the Core Devices SDK. gabbro and emery
are the two platforms with the Moddable XS engine (they run Alloy /
on-watch JavaScript); the classic platforms are C-only.
"""

from __future__ import annotations

PLATFORMS: dict[str, dict[str, object]] = {
    "aplite": {
        "display_name": "Pebble / Pebble Steel",
        "width": 144,
        "height": 168,
        "shape": "rect",
        "color": False,
        "touchscreen": False,
    },
    "basalt": {
        "display_name": "Pebble Time",
        "width": 144,
        "height": 168,
        "shape": "rect",
        "color": True,
        "touchscreen": False,
    },
    "chalk": {
        "display_name": "Pebble Time Round",
        "width": 180,
        "height": 180,
        "shape": "round",
        "color": True,
        "touchscreen": False,
    },
    "diorite": {
        "display_name": "Pebble 2",
        "width": 144,
        "height": 168,
        "shape": "rect",
        "color": False,
        "touchscreen": False,
    },
    "emery": {
        "display_name": "Pebble Time 2",
        "width": 200,
        "height": 228,
        "shape": "rect",
        "color": True,
        "touchscreen": True,
    },
    "flint": {
        "display_name": "Pebble (flint, B&W rect)",
        "width": 144,
        "height": 168,
        "shape": "rect",
        "color": False,
        "touchscreen": False,
    },
    "gabbro": {
        "display_name": "Pebble Round 2",
        "width": 260,
        "height": 260,
        "shape": "round",
        "color": True,
        "touchscreen": True,
    },
}


def platforms_resource() -> dict[str, dict[str, object]]:
    """Return the per-platform display spec table for the pebble://platforms resource."""
    return PLATFORMS
