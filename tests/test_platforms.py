"""Tests for pebble_mcp.platforms."""

from __future__ import annotations

from pebble_mcp.platforms import PLATFORMS, platforms_resource


def test_platforms_resource_returns_expected_keys():
    data = platforms_resource()
    assert set(data.keys()) == {"emery", "basalt", "chalk", "diorite"}


def test_emery_matches_repo_docs():
    # Verified against DESIGN.md ("E-paper realities (Time 2, 200x228)",
    # "200x228 with system status bar off") and ECOSYSTEM.md (emery-only
    # targetPlatforms).
    emery = PLATFORMS["emery"]
    assert emery["width"] == 200
    assert emery["height"] == 228
    assert emery["shape"] == "rect"
    assert emery["color"] is True
    assert emery["touchscreen"] is True


def test_chalk_is_round_and_others_are_not():
    assert PLATFORMS["chalk"]["shape"] == "round"
    assert PLATFORMS["basalt"]["shape"] == "rect"
    assert PLATFORMS["diorite"]["shape"] == "rect"


def test_diorite_is_black_and_white():
    assert PLATFORMS["diorite"]["color"] is False
    for name in ("emery", "basalt", "chalk"):
        assert PLATFORMS[name]["color"] is True
