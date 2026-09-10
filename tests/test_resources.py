"""Tests for pebble_mcp.resources: registration + shape of each pebble:// resource."""

from __future__ import annotations

import json

from mcp.server.fastmcp import FastMCP

from pebble_mcp.platforms import platforms_resource
from pebble_mcp.resources import (
    colors_resource,
    fonts_resource,
    register,
    touch_interaction_resource,
    wire_conventions_resource,
)

EXPECTED_URIS = {
    "pebble://platforms",
    "pebble://colors",
    "pebble://fonts",
    "pebble://wire-conventions",
    "pebble://touch-interaction",
}


def _server() -> FastMCP:
    mcp = FastMCP("test-pebble-mcp")
    register(mcp)
    return mcp


async def test_register_exposes_all_resource_uris():
    mcp = _server()
    resources = await mcp.list_resources()
    uris = {str(r.uri) for r in resources}
    assert EXPECTED_URIS <= uris


async def _read(mcp: FastMCP, uri: str) -> object:
    result = await mcp.read_resource(uri)
    # FastMCP's read_resource yields an iterable of readable-resource results.
    items = list(result)
    assert items, f"no content returned for {uri}"
    content = items[0].content if hasattr(items[0], "content") else items[0]
    return json.loads(content)


async def test_platforms_resource_matches_platforms_module():
    mcp = _server()
    data = await _read(mcp, "pebble://platforms")
    assert data == platforms_resource()


async def test_colors_resource_shape():
    mcp = _server()
    data = await _read(mcp, "pebble://colors")
    assert set(data.keys()) == {"colors", "roles"}
    assert len(data["colors"]) == 64
    assert isinstance(data["roles"], list)
    assert len(data["roles"]) > 0
    malachite = next(c for c in data["colors"] if c["name"] == "GColorMalachite")
    assert malachite["hex"] == "00FF55"
    for c in data["colors"]:
        assert set(c.keys()) == {"name", "hex", "corrected_hex", "argb8"}
    for role in data["roles"]:
        assert set(role.keys()) == {"role", "gcolor", "hex", "usage"}
    role_names = {r["role"] for r in data["roles"]}
    for expected in ("background", "accent", "good", "attention", "reserved"):
        assert expected in role_names


async def test_fonts_resource_shape():
    mcp = _server()
    data = await _read(mcp, "pebble://fonts")
    assert "fonts" in data
    assert isinstance(data["fonts"], list)
    assert len(data["fonts"]) > 0
    for f in data["fonts"]:
        assert set(f.keys()) == {
            "key",
            "c_key",
            "family",
            "points",
            "weight",
            "numbers_only",
            "emery_only",
            "note",
        }
    keys = {f["key"] for f in data["fonts"]}
    assert "LECO_42_NUMBERS" in keys
    leco = next(f for f in data["fonts"] if f["key"] == "LECO_42_NUMBERS")
    assert leco["numbers_only"] is True
    gothic = next(f for f in data["fonts"] if f["key"] == "GOTHIC_14")
    assert gothic["numbers_only"] is False


async def test_wire_conventions_resource_shape():
    mcp = _server()
    data = await _read(mcp, "pebble://wire-conventions")
    expected_keys = {
        "overview",
        "delimiters",
        "example",
        "sanitization",
        "integer_encoding",
        "staleness",
        "message_key",
        "bounded_copies",
    }
    assert expected_keys <= set(data.keys())
    assert data["delimiters"]["section"] == "~"
    assert data["delimiters"]["row"] == "|"
    assert data["delimiters"]["field"] == "^"
    # Generic, not coach/hail specific.
    blob = json.dumps(data).lower()
    assert "hailalert" not in blob
    assert "trainer.btf4e" not in blob


def test_colors_resource_function_matches_registered_resource():
    data = colors_resource()
    assert len(data["colors"]) == 64
    assert data["roles"]


def test_fonts_resource_function_returns_dict():
    data = fonts_resource()
    assert "fonts" in data


def test_wire_conventions_resource_function_returns_dict():
    data = wire_conventions_resource()
    assert "delimiters" in data


async def test_touch_interaction_resource_shape():
    mcp = _server()
    data = await _read(mcp, "pebble://touch-interaction")
    expected_keys = {
        "overview",
        "event_model",
        "swipe_recognition",
        "buttons_and_touch_coexist",
        "platform_gating",
        "lifecycle",
        "gotchas",
        "credit",
    }
    assert expected_keys <= set(data.keys())
    assert set(data["event_model"]) == {"touchdown", "position_update", "liftoff"}
    assert data["swipe_recognition"]["jitter_threshold_px"] == 18
    assert data["platform_gating"]["macro"] == "PBL_TOUCH"
    assert data["platform_gating"]["touch_platforms"] == ["emery", "gabbro"]
    assert isinstance(data["gotchas"], list) and data["gotchas"]


def test_touch_interaction_covers_the_named_techniques():
    data = touch_interaction_resource()
    blob = json.dumps(data).lower()
    for topic in (
        "liftoff",
        "touchdown",
        "dominant-axis",
        "touch_service_is_enabled",
        "multi-click",
        "unsubscribe",
    ):
        assert topic in blob, topic
    # BACK's firmware exit race is called out explicitly.
    assert any("back" in g.lower() and "firmware" in g.lower() for g in data["gotchas"])


def test_touch_interaction_credits_upstream_and_flags_the_gpl():
    # pebble-mcp is MIT; the resource describes patterns and must credit the
    # GPL-3.0 app they were observed in rather than carrying its code.
    credit = touch_interaction_resource()["credit"]
    assert "github.com/lanrat/pebble-2048-touch" in credit
    assert "GPL-3.0" in credit
    assert "6df87b64b7174448a065ef54" in credit
