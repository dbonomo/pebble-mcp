"""Per-platform Pebble display specs.

Emery (Pebble Time 2) numbers verified against this repo's DESIGN.md
("E-paper realities (Time 2, 200x228)", "200x228 with system status bar
off") and ECOSYSTEM.md (emery-only targetPlatforms throughout). Basalt,
chalk, and diorite specs are standard public Pebble SDK platform
definitions (not project-specific), included for completeness.
"""

from __future__ import annotations

PLATFORMS: dict[str, dict[str, object]] = {
    "emery": {
        "display_name": "Pebble Time 2",
        "width": 200,
        "height": 228,
        "shape": "rect",
        "color": True,
        "touchscreen": True,
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
}


def platforms_resource() -> dict[str, dict[str, object]]:
    """Return the per-platform display spec table for the pebble://platforms resource."""
    return PLATFORMS
