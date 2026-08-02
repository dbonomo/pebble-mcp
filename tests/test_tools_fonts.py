"""Registration + smoke tests for pebble_mcp.tools_fonts (font_plan and
pdc_convert as MCP tools)."""

from __future__ import annotations

import base64
import json

import pytest
from mcp.server.fastmcp import FastMCP

from pebble_mcp.tools_fonts import register


@pytest.fixture()
def mcp() -> FastMCP:
    server = FastMCP("test-pebble-mcp")
    register(server)
    return server


async def test_register_adds_both_tools(mcp: FastMCP):
    tools = await mcp.list_tools()
    names = {t.name for t in tools}
    assert {"font_plan", "pdc_convert"} <= names


async def test_registered_tools_have_descriptions(mcp: FastMCP):
    tools = {t.name: t for t in await mcp.list_tools()}
    for name in ("font_plan", "pdc_convert"):
        assert tools[name].description
        assert len(tools[name].description) > 20


async def test_font_plan_tool_call_role(mcp: FastMCP):
    result = await mcp.call_tool("font_plan", {"role_or_text": "hero numerals"})
    payload = _content_json(result)
    assert payload["mode"] == "role"
    assert payload["role"] == "hero numerals"


async def test_font_plan_tool_call_text(mcp: FastMCP):
    result = await mcp.call_tool("font_plan", {"role_or_text": "15:37"})
    payload = _content_json(result)
    assert payload["mode"] == "text"
    assert payload["minimal_character_regex"] == "[1357:]"


async def test_pdc_convert_tool_valid_svg_returns_base64(mcp: FastMCP):
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10" '
        'viewBox="0 0 10 10"><rect x="1" y="1" width="8" height="8" fill="#FFFFFF"/></svg>'
    )
    result = await mcp.call_tool("pdc_convert", {"svg_text": svg})
    payload = _content_json(result)
    assert payload["valid"] is True
    assert payload["violations"] == []
    raw = base64.b64decode(payload["pdc_base64"])
    assert raw[:4] == b"PDCI"
    assert payload["size_bytes"] == len(raw)


async def test_pdc_convert_tool_invalid_svg_returns_violations_no_base64(mcp: FastMCP):
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10">'
        '<text x="0" y="5">hi</text></svg>'
    )
    result = await mcp.call_tool("pdc_convert", {"svg_text": svg})
    payload = _content_json(result)
    assert payload["valid"] is False
    assert payload["violations"]
    assert "pdc_base64" not in payload


def _content_json(call_tool_result) -> dict:
    """FastMCP's call_tool returns (content_blocks, structured_dict) in
    recent SDK versions, or just content blocks in older ones. Prefer the
    structured result; fall back to parsing the first text block."""
    if isinstance(call_tool_result, tuple):
        content, structured = call_tool_result
        if structured is not None:
            return structured
    else:
        content = call_tool_result
    for block in content:
        text = getattr(block, "text", None)
        if text is not None:
            return json.loads(text)
    raise AssertionError(f"no text content block in {call_tool_result!r}")
