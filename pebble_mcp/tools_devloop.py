"""Tier-3 dev-loop MCP tools: build, install, emulator lifecycle.

Wires :mod:`pebble_mcp.devloop` into the server (the flow/screenshot tools
live in :mod:`pebble_mcp.tools_flow`). Everything here needs the ``pebble``
CLI on PATH; each tool surfaces devloop's actionable gating error when it is
absent. Paths are resolved against the workspace root (see
:func:`pebble_mcp.tools_flow.repo_root`) when relative.
"""

from __future__ import annotations

import dataclasses
import os

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError

from pebble_mcp import devloop
from pebble_mcp.tools_flow import repo_root


def _resolve(path: str) -> str:
    if "\x00" in path:
        raise ToolError("path contains a null byte, which is never valid")
    return path if os.path.isabs(path) else os.path.join(repo_root(), path)


def _asdict(result: object) -> dict[str, object]:
    return dataclasses.asdict(result)  # type: ignore[call-overload]


def register(mcp: FastMCP) -> None:
    """Register the dev-loop tools on ``mcp``."""

    @mcp.tool()
    def pebble_build(project_dir: str) -> dict[str, object]:
        """Build a Pebble app project and return structured results.

        Runs `pebble build` in project_dir (absolute, or relative to the
        workspace root). Returns success, the produced .pbw path, and
        compiler errors/warnings as structured file:line diagnostics.
        Requires the `pebble` CLI on PATH (see capabilities()).
        """
        try:
            return _asdict(devloop.build(_resolve(project_dir)))
        except devloop.PebbleUnavailableError as e:
            raise ToolError(str(e)) from e

    @mcp.tool()
    def pebble_install(target: str, platform: str = "emery") -> dict[str, object]:
        """Install an app into the emulator, starting it if needed.

        target is either a project directory (built first if needed) or a
        path to a prebuilt .pbw file; relative paths resolve against the
        workspace root. The emulator is killed and wiped first — the
        reliable-launch lesson — so expect a few seconds of startup.
        Requires the `pebble` CLI on PATH.
        """
        try:
            return _asdict(devloop.install(_resolve(target), platform))
        except devloop.PebbleUnavailableError as e:
            raise ToolError(str(e)) from e

    @mcp.tool()
    def emu_start(platform: str = "emery") -> dict[str, object]:
        """Boot the Pebble emulator for the given platform (default emery).

        Idempotent-ish: if one is already running this reconnects/restarts.
        Requires the `pebble` CLI on PATH.
        """
        try:
            return _asdict(devloop.emu_start(platform))
        except devloop.PebbleUnavailableError as e:
            raise ToolError(str(e)) from e

    @mcp.tool()
    def emu_stop(wipe: bool = False) -> dict[str, object]:
        """Shut down the running emulator; wipe=True also clears its storage.

        Wiping is the fix for most emulator flakiness (apps not launching,
        stale state). Requires the `pebble` CLI on PATH.
        """
        try:
            return _asdict(devloop.emu_stop(wipe))
        except devloop.PebbleUnavailableError as e:
            raise ToolError(str(e)) from e
