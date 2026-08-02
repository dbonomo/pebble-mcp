"""Tests for the pebble_mcp server object: registration and end-to-end stdio."""

from __future__ import annotations

import json

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from pebble_mcp.server import mcp


async def test_server_registers_expected_tool():
    tools = await mcp.list_tools()
    names = {t.name for t in tools}
    assert "capabilities" in names


async def test_server_registers_expected_resource():
    resources = await mcp.list_resources()
    uris = {str(r.uri) for r in resources}
    assert "pebble://platforms" in uris


def test_server_name():
    assert mcp.name == "pebble-mcp"


async def test_stdio_initialize_and_call_tool():
    """Full end-to-end check: spawn the real console entry point over stdio,
    perform the MCP initialize handshake, and call capabilities()."""
    params = StdioServerParameters(command="uv", args=["run", "pebble-mcp"])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            init_result = await session.initialize()
            assert init_result.serverInfo.name == "pebble-mcp"

            tool_result = await session.call_tool("capabilities", {})
            assert not tool_result.isError
            payload = json.loads(tool_result.content[0].text)
            assert payload["tier1_appstore"] is True
            assert "version" in payload

            resource_result = await session.read_resource("pebble://platforms")
            data = json.loads(resource_result.contents[0].text)
            assert data["emery"]["width"] == 200
            assert data["emery"]["height"] == 228
