"""Tests for the dev-loop MCP tools (tools_devloop)."""

from __future__ import annotations

import pytest
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError

from pebble_mcp import devloop, tools_devloop

EXPECTED_TOOLS = {"pebble_build", "pebble_install", "emu_start", "emu_stop"}


def _server() -> FastMCP:
    mcp = FastMCP("test")
    tools_devloop.register(mcp)
    return mcp


async def test_registration_exposes_expected_tools():
    tools = await _server().list_tools()
    assert {t.name for t in tools} == EXPECTED_TOOLS
    for t in tools:
        assert t.description


def test_resolve_rejects_null_byte():
    with pytest.raises(ToolError):
        tools_devloop._resolve("foo\x00bar")


def test_resolve_relative_joins_repo_root(monkeypatch):
    monkeypatch.setenv("PEBBLE_MCP_REPO_ROOT", "/some/root")
    assert tools_devloop._resolve("hail-watch") == "/some/root/hail-watch"
    assert tools_devloop._resolve("/abs/path") == "/abs/path"


async def test_tools_gate_on_missing_pebble(monkeypatch):
    monkeypatch.setattr(devloop.shutil, "which", lambda _: None)
    mcp = _server()
    for name, args in [
        ("pebble_build", {"project_dir": "x"}),
        ("pebble_install", {"target": "x"}),
        ("emu_start", {}),
        ("emu_stop", {}),
    ]:
        with pytest.raises(ToolError, match="pebble"):
            await mcp.call_tool(name, args)


async def test_emu_stop_passes_through_result(monkeypatch):
    monkeypatch.setattr(devloop.shutil, "which", lambda _: "/usr/bin/pebble")

    def fake_stop(wipe=False, **kwargs):
        return devloop.EmuResult(ok=True, platform="emery", returncode=0, wiped=wipe)

    monkeypatch.setattr(devloop, "emu_stop", fake_stop)
    result = await _server().call_tool("emu_stop", {"wipe": True})
    payload = result[1] if isinstance(result, tuple) else result
    assert payload["wiped"] is True and payload["ok"] is True
