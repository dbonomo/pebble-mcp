"""Tests for pebble_mcp.tools_project (MCP registration wrappers)."""

from __future__ import annotations

import asyncio
import json
import os

from mcp.server.fastmcp import FastMCP

from pebble_mcp import tools_project


def _make_server() -> FastMCP:
    mcp = FastMCP("test")
    tools_project.register(mcp)
    return mcp


def _tool_names(mcp: FastMCP) -> set[str]:
    return {t.name for t in asyncio.run(mcp.list_tools())}


def test_register_exposes_all_four_tools():
    names = _tool_names(_make_server())
    assert {
        "project_new",
        "project_info",
        "project_add_resource",
        "project_set_meta",
    } <= names


def test_all_tools_have_docstrings():
    mcp = _make_server()
    for tool in asyncio.run(mcp.list_tools()):
        if tool.name.startswith("project_"):
            assert tool.description and len(tool.description) > 40


def _call(mcp: FastMCP, tool_name: str, **kwargs):
    result = asyncio.run(mcp.call_tool(tool_name, kwargs))
    # FastMCP's call_tool returns (content_blocks, structured_dict) in recent
    # SDK versions, or just content blocks in older ones.
    if isinstance(result, tuple):
        content, structured = result
        if structured is not None:
            return structured
    else:
        content = result
    for block in content:
        text = getattr(block, "text", None)
        if text is not None:
            return json.loads(text)
    raise AssertionError(f"no text content block in {result!r}")


def test_project_new_and_info_via_tools(tmp_path):
    mcp = _make_server()
    new = _call(mcp, "project_new", name="Tooly", language="c", dest_dir=str(tmp_path))
    assert new["kind"] == "watchface"
    assert os.path.isfile(os.path.join(new["path"], "package.json"))

    info = _call(mcp, "project_info", dir=new["path"])
    assert info["uuid"] == new["uuid"]
    assert info["displayName"] == "Tooly"


def test_project_set_meta_via_tools(tmp_path):
    mcp = _make_server()
    new = _call(mcp, "project_new", name="Metay", language="c", dest_dir=str(tmp_path))
    blk = _call(mcp, "project_set_meta", dir=new["path"], message_keys=["A"])
    assert blk["messageKeys"] == ["A"]
