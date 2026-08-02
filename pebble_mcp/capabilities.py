"""Tiered capability detection.

Tier 1 (appstore) is pure HTTPS and always available. Tier 2 (design
toolkit) needs Pillow. Tier 3 (dev loop) needs the `pebble` CLI on PATH.
Tier 4 (authenticated) needs PEBBLE_API_TOKEN in the environment.
"""

from __future__ import annotations

import os
import shutil

from pebble_mcp import __version__


def pillow_available() -> bool:
    """True if Pillow can be imported. The Tier-2 design tools gate on this.

    Probed fresh on every call (never cached) so a stubbed/broken Pillow is
    detected at tool-call time, not frozen at import time. Any failure importing
    ``PIL`` — not just a plain ``ImportError`` — counts as unavailable, so a
    half-installed or shadowed Pillow degrades cleanly instead of surfacing a
    raw traceback.
    """
    try:
        import PIL  # noqa: F401
    except Exception:
        return False
    return True


def _tier2_available() -> bool:
    return pillow_available()


def _tier3_available() -> bool:
    return shutil.which("pebble") is not None


def _tier4_available() -> bool:
    return bool(os.environ.get("PEBBLE_API_TOKEN"))


def get_capabilities() -> dict[str, object]:
    """Report which capability tiers are live in the current environment."""
    return {
        "tier1_appstore": True,
        "tier2_design": _tier2_available(),
        "tier3_devloop": _tier3_available(),
        "tier4_auth": _tier4_available(),
        "version": __version__,
    }
