"""Tests for pebble_mcp.platforms."""

from __future__ import annotations

from pebble_mcp.platforms import PLATFORMS, platforms_resource

ALL_SEVEN = {"aplite", "basalt", "chalk", "diorite", "emery", "flint", "gabbro"}


def test_platforms_resource_returns_all_seven():
    data = platforms_resource()
    assert set(data.keys()) == ALL_SEVEN


def test_every_platform_has_sane_specs():
    for name, spec in PLATFORMS.items():
        assert isinstance(spec["display_name"], str) and spec["display_name"]
        assert 100 < spec["width"] <= 300, name
        assert 100 < spec["height"] <= 300, name
        assert spec["shape"] in ("rect", "round"), name
        assert isinstance(spec["color"], bool), name
        assert isinstance(spec["touchscreen"], bool), name


def test_emery_matches_repo_docs():
    # Verified against DESIGN.md and the SDK platform table (200x228, touch).
    emery = PLATFORMS["emery"]
    assert emery["width"] == 200
    assert emery["height"] == 228
    assert emery["shape"] == "rect"
    assert emery["color"] is True
    assert emery["touchscreen"] is True


def test_round_platforms_are_chalk_and_gabbro():
    round_platforms = {n for n, s in PLATFORMS.items() if s["shape"] == "round"}
    assert round_platforms == {"chalk", "gabbro"}
    # chalk is the original round; gabbro is the Round 2 platform.
    assert PLATFORMS["chalk"]["width"] == 180
    assert PLATFORMS["gabbro"]["width"] == 260
    assert PLATFORMS["gabbro"]["height"] == 260
    assert PLATFORMS["gabbro"]["color"] is True
    assert PLATFORMS["gabbro"]["touchscreen"] is True


def test_black_and_white_platforms():
    bw = {n for n, s in PLATFORMS.items() if not s["color"]}
    assert bw == {"aplite", "diorite", "flint"}


def test_flint_is_bw_rect_144x168():
    flint = PLATFORMS["flint"]
    assert flint["width"] == 144
    assert flint["height"] == 168
    assert flint["shape"] == "rect"
    assert flint["color"] is False
    assert flint["touchscreen"] is False


def test_classic_dimensions():
    for name in ("aplite", "basalt", "diorite", "flint"):
        assert PLATFORMS[name]["width"] == 144
        assert PLATFORMS[name]["height"] == 168
