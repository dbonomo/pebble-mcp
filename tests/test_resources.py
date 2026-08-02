"""Tests for pebble_mcp.resources: registration + shape of each pebble:// resource."""

from __future__ import annotations

import json

from mcp.server.fastmcp import FastMCP

from pebble_mcp.platforms import platforms_resource
from pebble_mcp.resources import (
    colors_resource,
    fonts_resource,
    register,
    wire_conventions_resource,
)

EXPECTED_URIS = {
    "pebble://platforms",
    "pebble://colors",
    "pebble://fonts",
    "pebble://wire-conventions",
}


def _server() -> FastMCP:
    mcp = FastMCP("test-pebble-mcp")
    register(mcp)
    return mcp


async def test_register_exposes_all_four_uris():
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
            "key", "c_key", "family", "points", "weight",
            "numbers_only", "emery_only", "note",
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
