"""Environment-degradation matrix for pebble-mcp (roadmap task 2.1).

We can't run Claude Desktop / claude.ai here, so this covers what *is* testable
locally: each capability tier can be absent independently, and in every one of
those states the server must still import, register all its tools, complete an
MCP initialize handshake, and hand back an *actionable* error (never a raw
traceback) from any tool that can't run.

Matrix (state × asserted behavior):

* Tier 3 absent (no ``pebble`` on PATH): ``capabilities().tier3_devloop`` is
  False; every emulator-touching tool raises a ToolError naming ``pebble`` /
  PATH; import + registration still succeed.
* Tier 2 absent (Pillow un-importable, simulated via ``sys.modules`` stubbing):
  ``capabilities().tier2_design`` is False; the pixel tools raise a clear
  "install Pillow" ToolError — NOT an ImportError; the pure-math tools
  (``color_nearest``) still work; the server still imports and initializes over
  real stdio.
* Tier 4 absent (no ``PEBBLE_API_TOKEN``): ``capabilities().tier4_auth`` is
  False.
"""

from __future__ import annotations

import json
import os
import sys

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError

from pebble_mcp import devloop, tools_design, tools_flow
from pebble_mcp.capabilities import get_capabilities, pillow_available

# Path to the pebble-mcp project dir (parent of the tests/ dir).
PKG_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# --------------------------------------------------------------------------- #
# Fixtures: force a tier absent
# --------------------------------------------------------------------------- #
@pytest.fixture
def no_pebble(monkeypatch, tmp_path):
    """Tier 3 absent: no ``pebble`` executable resolvable on PATH."""
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setattr(devloop.shutil, "which", lambda _name: None)


@pytest.fixture
def no_pillow(monkeypatch):
    """Tier 2 absent: make ``import PIL`` fail everywhere it is (re)attempted.

    Setting ``sys.modules['PIL'] = None`` makes any fresh ``import PIL`` raise
    ImportError, which is exactly how a host that shipped without the design
    extra behaves. ``pillow_available()`` / ``_require_pillow()`` re-probe on
    every call, so this flips them without touching already-imported modules.
    """
    monkeypatch.setitem(sys.modules, "PIL", None)


# --------------------------------------------------------------------------- #
# Tier 3 absent — no pebble-tool on PATH
# --------------------------------------------------------------------------- #
def test_tier3_capability_false_without_pebble(no_pebble):
    assert get_capabilities()["tier3_devloop"] is False


def test_tier3_tools_gate_with_actionable_error(no_pebble):
    """Every emulator-touching tool refuses with a ToolError that names the fix,
    rather than crashing or attempting a subprocess call."""
    with pytest.raises(ToolError, match="pebble"):
        tools_flow.run_flow_tool("app demo\nshot s\n")
    with pytest.raises(ToolError, match="pebble"):
        tools_flow.screenshot_tool()
    with pytest.raises(ToolError, match="pebble"):
        tools_flow.input_tool("press", "select")
    with pytest.raises(ToolError, match="pebble"):
        tools_flow.logs_tool(seconds=1.0)


def test_tier3_error_is_actionable(no_pebble):
    """The gating error tells an agent how to recover (install + PATH)."""
    with pytest.raises(ToolError) as exc:
        tools_flow.screenshot_tool()
    msg = str(exc.value)
    assert "PATH" in msg
    assert "install" in msg.lower()


def test_tier3_absent_import_and_registration_survive(no_pebble):
    """No pebble on PATH must not break import or tool registration: the Tier-3
    tools are still *registered* (they just fail actionably when called)."""
    import pebble_mcp.server  # noqa: F401 - importing must not raise

    app = FastMCP("degradation-tier3")
    tools_flow.register(app)  # must not raise


async def test_tier3_absent_tools_still_listed(no_pebble):
    app = FastMCP("degradation-tier3-list")
    tools_flow.register(app)
    names = {t.name for t in await app.list_tools()}
    assert {"flow_run", "emu_screenshot", "emu_input", "emu_logs"} <= names


# --------------------------------------------------------------------------- #
# Tier 2 absent — Pillow un-importable
# --------------------------------------------------------------------------- #
def test_pillow_available_reflects_stub(no_pillow):
    assert pillow_available() is False


def test_tier2_capability_false_without_pillow(no_pillow):
    assert get_capabilities()["tier2_design"] is False


def test_tier2_capability_true_when_pillow_present():
    # Sanity: with the real (installed) Pillow, the tier reports live.
    assert pillow_available() is True
    assert get_capabilities()["tier2_design"] is True


async def test_tier2_pixel_tools_raise_clear_error_not_importerror(no_pillow):
    """image_quantize / image_prep / palette_swatch must surface a clean
    'install Pillow' ToolError, never an ImportError traceback."""
    app = FastMCP("degradation-tier2")
    tools_design.register(app)

    import base64

    tiny_png = base64.b64encode(
        b"\x89PNG\r\n\x1a\n"  # not a real image, but the gate fires before decode
    ).decode()

    for name, args in (
        ("image_quantize", {"source": tiny_png}),
        ("image_prep", {"source": tiny_png}),
        ("palette_swatch", {"colors": ["#FF0000"]}),
    ):
        with pytest.raises(ToolError, match="[Pp]illow"):
            await app.call_tool(name, args)


def test_tier2_require_pillow_raises_toolerror(no_pillow):
    with pytest.raises(ToolError) as exc:
        tools_design._require_pillow()
    assert not isinstance(exc.value, ImportError)
    assert "install" in str(exc.value).lower()


async def test_tier2_pure_math_tool_still_works_without_pillow(no_pillow):
    """color_nearest is pure palette math: it must keep working with no Pillow."""
    app = FastMCP("degradation-tier2-pure")
    tools_design.register(app)
    result = await app.call_tool("color_nearest", {"color": "#01FE02"})
    content = result[0] if isinstance(result, tuple) else result
    payload = json.loads(content[0].text)
    assert payload["name"] == "GColorGreen"


# --------------------------------------------------------------------------- #
# Tier 4 absent — no token
# --------------------------------------------------------------------------- #
def test_tier4_capability_false_without_token(monkeypatch):
    monkeypatch.delenv("PEBBLE_API_TOKEN", raising=False)
    assert get_capabilities()["tier4_auth"] is False


def test_tier4_capability_true_with_token(monkeypatch):
    monkeypatch.setenv("PEBBLE_API_TOKEN", "sekrit")
    assert get_capabilities()["tier4_auth"] is True


# --------------------------------------------------------------------------- #
# Server startup robustness under Pillow-absent (requirement 2)
# --------------------------------------------------------------------------- #
async def test_stdio_initialize_and_list_tools_without_pillow():
    """Real stdio handshake with Pillow forced un-importable in the *server*
    process: initialize + list_tools + capabilities must all succeed, proving
    the server has no import-time Pillow dependence.

    The server subprocess is a plain interpreter that pre-empts ``import PIL``
    (``sys.modules['PIL'] = None``) before importing the server — the same
    end-state as a host that never installed Pillow.
    """
    bootstrap = (
        "import sys; sys.modules['PIL'] = None; "
        "from pebble_mcp.server import main; main()"
    )
    params = StdioServerParameters(command=sys.executable, args=["-c", bootstrap])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            init_result = await session.initialize()
            assert init_result.serverInfo.name == "pebble-mcp"

            tools = await session.list_tools()
            names = {t.name for t in tools.tools}
            # Tools from every tier are registered regardless of Pillow.
            assert {"capabilities", "image_quantize", "flow_run"} <= names

            caps = await session.call_tool("capabilities", {})
            assert not caps.isError
            payload = json.loads(caps.content[0].text)
            assert payload["tier2_design"] is False
            assert payload["tier1_appstore"] is True


# --------------------------------------------------------------------------- #
# Cold start — exactly what a host's .mcp.json runs (requirement 3)
# --------------------------------------------------------------------------- #
async def test_cold_start_uv_run_from_foreign_cwd(tmp_path):
    """`uv run --directory <pebble-mcp> pebble-mcp` must boot from an unrelated
    working directory — this is precisely what a host launches from its
    .mcp.json. Spawn it, complete initialize + a capabilities call, then let the
    stdio client tear the process down.
    """
    params = StdioServerParameters(
        command="uv",
        args=["run", "--directory", PKG_DIR, "pebble-mcp"],
        cwd=str(tmp_path),
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            init_result = await session.initialize()
            assert init_result.serverInfo.name == "pebble-mcp"
            caps = await session.call_tool("capabilities", {})
            payload = json.loads(caps.content[0].text)
            assert payload["tier1_appstore"] is True
            assert "version" in payload
