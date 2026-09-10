"""Tier-3 MCP tools: run flow specs and drive the running emulator (roadmap C3).

This module wires the flow engine (:mod:`pebble_mcp.flow`) and the dev-loop
wrappers (:mod:`pebble_mcp.devloop`) into MCP tools. It owns *no* new emulator
logic beyond what those libraries already provide — it is the presentation /
protocol layer that turns a parsed-and-run flow into MCP content and exposes
the one-off emulator conveniences (screenshot / input / logs).

``register(mcp)`` attaches five tools to a :class:`FastMCP` instance:

* ``flow_run`` — parse + run a flow, return per-shot metadata **and** the shots
  as MCP images.
* ``flow_validate`` — parse-only pre-check (cheap; no emulator needed).
* ``emu_screenshot`` — one-off screenshot of the running emulator.
* ``emu_input`` — press / longpress / tap the emulator buttons.
* ``emu_logs`` — bounded log capture.

**Image return in the MCP SDK (mcp 1.28.1).** FastMCP's ``_convert_to_content``
converts a tool's return value to protocol content by recursing into
``list``/``tuple`` items: a ``dict`` becomes a JSON ``TextContent`` block and
each :class:`mcp.server.fastmcp.Image` becomes an ``ImageContent`` block. So a
mixed, multi-image return is simply ``[metadata_dict, Image, Image, ...]`` —
one text block of structured metadata followed by one image block per shot.
``flow_run`` is registered with ``structured_output=False`` so the SDK keeps
this hand-built unstructured content instead of trying to derive an output
schema from the ``list`` return annotation. This is the approach that works on
1.28.1 and is exercised end-to-end in ``tests/test_tools_flow.py``.

Testability: the impl functions below take an injectable ``runner`` (the same
:data:`~pebble_mcp.flow.PebbleRunner` seam the engine uses) and ``sleep`` so
tests drive them with a stub ``pebble`` and never touch a real emulator. The
``register`` wrappers expose the exact public tool signatures and delegate to
these impls with the real subprocess runner.
"""

from __future__ import annotations

import os
import tempfile
import time
from collections.abc import Callable
from typing import Any

from mcp.server.fastmcp import FastMCP, Image
from mcp.server.fastmcp.exceptions import ToolError

from . import devloop
from .flow import (
    BUTTON_SETTLE_SEC,
    VALID_BUTTONS,
    FlowParseError,
    PebbleRunner,
    ShotStep,
    _is_png,
    looks_wedged,
    parse_flow,
    run_flow,
    subprocess_runner,
)

# Runaway guard: a single flow_run may capture at most this many shots. A flow
# asking for more is almost certainly a mistake (or an attempt to wedge the
# emulator with an endless capture loop) and is refused before any install.
MAX_SHOTS = 40

# Valid actions for emu_input.
VALID_ACTIONS = ("press", "longpress", "tap")

# Upper bound on a longpress hold. Real button holds are well under a second;
# an absurd value is almost certainly a mistake and is refused up front.
MAX_DURATION_MS = 60_000


# --------------------------------------------------------------------------- #
# Repo-root + gating helpers
# --------------------------------------------------------------------------- #
def repo_root() -> str:
    """Resolve the repository root the flow engine installs project dirs against.

    Defaults to the parent of the ``pebble-mcp`` package dir at runtime — i.e.
    two levels up from this file (``pebble_mcp/`` -> ``pebble-mcp/`` ->
    repo root), which is where sibling Pebble project directories live.
    Overridable via the ``PEBBLE_MCP_REPO_ROOT`` environment variable so the
    server can run against a checkout elsewhere on disk.
    """
    override = os.environ.get("PEBBLE_MCP_REPO_ROOT")
    if override:
        return os.path.abspath(override)
    pkg_dir = os.path.dirname(os.path.abspath(__file__))  # .../pebble-mcp/pebble_mcp
    project_dir = os.path.dirname(pkg_dir)  # .../pebble-mcp
    return os.path.dirname(project_dir)  # repo root (parent of pebble-mcp)


def _require_emulator() -> None:
    """Gate emulator-touching tools on the ``pebble`` CLI being available."""
    if not devloop.pebble_available():
        raise ToolError(
            "no Pebble emulator tooling available: the 'pebble' CLI was not "
            "found on PATH. Install it with `uv tool install pebble-tool` and "
            "ensure ~/.local/bin is on PATH, then use flow_run (its `app`/`pbw` "
            "steps install onto the emulator, which boots it as a side effect) "
            "before calling emu_screenshot/emu_input/emu_logs directly."
        )


# --------------------------------------------------------------------------- #
# flow_run
# --------------------------------------------------------------------------- #
def run_flow_tool(
    flow_text: str,
    out_dir: str | None = None,
    *,
    runner: PebbleRunner = subprocess_runner,
    sleep: Callable[[float], None] = time.sleep,
) -> list[Any]:
    """Implementation behind the ``flow_run`` tool (see ``register`` for docs)."""
    _require_emulator()

    try:
        flow = parse_flow(flow_text)
    except FlowParseError as e:
        # The engine already reports the 1-based line number in the message.
        raise ToolError(f"flow parse error: {e}") from e

    n_shots = sum(1 for s in flow.steps if isinstance(s, ShotStep))
    if n_shots > MAX_SHOTS:
        raise ToolError(
            f"flow requests {n_shots} shots, over the {MAX_SHOTS}-shot cap "
            "(runaway guard). Split it into smaller flows."
        )

    if out_dir is None:
        out_dir = tempfile.mkdtemp(prefix="pebble-flow-")

    try:
        result = run_flow(flow, out_dir, repo_root=repo_root(), runner=runner, sleep=sleep)
    except Exception as e:  # Wedged, or any driver failure -> actionable tool error
        raise ToolError(
            f"flow run failed: {e}. The emulator may be wedged; try emu_stop(wipe=True) and re-run."
        ) from e

    metadata: dict[str, Any] = {
        "flow": result.name,
        "out_dir": out_dir,
        "shot_count": len(result.shots),
        "retries": result.retries,
        "wedge_recoveries": result.wedge_recoveries,
        "restarted": result.restarted,
        "shots": [
            {
                "app": s.app,
                "index": s.index,
                "name": s.name,
                "filename": s.filename,
                "path": s.path,
                "duration_s": round(s.duration_s, 3),
            }
            for s in result.shots
        ],
    }
    # Mixed multi-image return: one JSON text block, then one image per shot.
    content: list[Any] = [metadata]
    content.extend(Image(path=s.path) for s in result.shots)
    return content


# --------------------------------------------------------------------------- #
# flow_validate
# --------------------------------------------------------------------------- #
def validate_flow_tool(flow_text: str) -> dict[str, Any]:
    """Implementation behind ``flow_validate``: parse-only, no emulator."""
    try:
        flow = parse_flow(flow_text)
    except FlowParseError as e:
        raise ToolError(f"flow parse error: {e}") from e

    counts: dict[str, int] = {}
    for step in flow.steps:
        key = type(step).__name__
        counts[key] = counts.get(key, 0) + 1
    n_shots = counts.get(ShotStep.__name__, 0)
    return {
        "valid": True,
        "step_count": len(flow.steps),
        "shot_count": n_shots,
        "over_shot_cap": n_shots > MAX_SHOTS,
        "steps_by_type": counts,
    }


# --------------------------------------------------------------------------- #
# emu_screenshot
# --------------------------------------------------------------------------- #
def screenshot_tool(
    name: str | None = None,
    *,
    runner: PebbleRunner = subprocess_runner,
) -> Image:
    """Implementation behind ``emu_screenshot``: one-off screenshot -> Image."""
    _require_emulator()
    base = (name or "screenshot").strip() or "screenshot"
    if not base.endswith(".png"):
        base += ".png"
    dest = os.path.join(tempfile.mkdtemp(prefix="pebble-shot-"), os.path.basename(base))
    rc, out = runner(["pebble", "screenshot", "--no-open", dest], None, 90)
    if rc != 0 or not os.path.exists(dest) or os.path.getsize(dest) == 0 or not _is_png(dest):
        hint = (
            " The emulator looks wedged; try emu_stop(wipe=True) then reinstall."
            if looks_wedged(out)
            else " Is an emulator running? Start one before screenshotting."
        )
        raise ToolError(f"screenshot failed (rc={rc}): {out[-200:].strip()}.{hint}")
    return Image(path=dest)


# --------------------------------------------------------------------------- #
# emu_input
# --------------------------------------------------------------------------- #
def input_tool(
    action: str,
    button: str | None = None,
    duration_ms: int | None = None,
    *,
    runner: PebbleRunner = subprocess_runner,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Implementation behind ``emu_input``: validated press / longpress / tap."""
    _require_emulator()

    if action not in VALID_ACTIONS:
        raise ToolError(f"invalid action {action!r}; expected one of {VALID_ACTIONS}")

    if action == "tap":
        if button is not None or duration_ms is not None:
            raise ToolError("'tap' takes no button or duration_ms")
        cmd = ["pebble", "emu-tap"]
    else:
        if button not in VALID_BUTTONS:
            raise ToolError(f"invalid button {button!r}; expected one of {VALID_BUTTONS}")
        cmd = ["pebble", "emu-button", "click", button]
        if action == "longpress":
            if duration_ms is None:
                raise ToolError("'longpress' requires duration_ms")
            if duration_ms <= 0:
                raise ToolError(f"duration_ms must be positive, got {duration_ms}")
            if duration_ms > MAX_DURATION_MS:
                raise ToolError(f"duration_ms {duration_ms} exceeds the {MAX_DURATION_MS} ms cap")
            cmd += ["--duration", str(duration_ms)]
        elif duration_ms is not None:
            raise ToolError("'press' does not take duration_ms; use 'longpress'")

    rc, out = runner(cmd, None, 60)
    if rc != 0:
        hint = (
            " The emulator looks wedged; try emu_stop(wipe=True)."
            if looks_wedged(out)
            else " Is an emulator running?"
        )
        raise ToolError(f"emu_input failed (rc={rc}): {out[-200:].strip()}.{hint}")
    # Pace: the emery emulator desyncs on back-to-back presses, so settle before
    # returning control — an agent firing rapid emu_input calls stays in sync.
    sleep(BUTTON_SETTLE_SEC)
    return {
        "ok": True,
        "action": action,
        "button": button,
        "duration_ms": duration_ms,
    }


# --------------------------------------------------------------------------- #
# emu_logs
# --------------------------------------------------------------------------- #
def logs_tool(
    seconds: float = 10.0,
    until_pattern: str | None = None,
    max_bytes: int = 65536,
    *,
    runner: PebbleRunner = subprocess_runner,
) -> dict[str, Any]:
    """Implementation behind ``emu_logs``: bounded capture via devloop."""
    _require_emulator()
    res = devloop.logs_capture(
        seconds=seconds,
        until_pattern=until_pattern,
        max_bytes=max_bytes,
        runner=runner,
    )
    return {
        "text": res.text,
        "returncode": res.returncode,
        "truncated": res.truncated,
        "matched": res.matched,
    }


# --------------------------------------------------------------------------- #
# Registration
# --------------------------------------------------------------------------- #
def register(mcp: FastMCP) -> None:
    """Attach the Tier-3 flow / emulator tools to ``mcp``."""

    @mcp.tool(structured_output=False)
    def flow_run(flow_text: str, out_dir: str | None = None) -> list[Any]:
        """Run a gallery-style flow spec against the emery emulator and return
        every screenshot.

        Parses ``flow_text`` (see pebble_mcp.flow for the line format), installs
        each ``app``/``pbw`` onto the emulator with the kill+wipe-first
        discipline, drives the buttons/waits, and captures the ``shot`` steps.
        Project dirs in ``app`` steps resolve against the repo root (parent of
        the pebble-mcp package dir, overridable via ``PEBBLE_MCP_REPO_ROOT``).
        ``out_dir`` defaults to a fresh tempdir.

        Returns a JSON metadata block — flow name, out_dir, per-shot
        {app, name, path, duration_s}, plus ``retries`` and
        ``wedge_recoveries`` — followed by one MCP image per shot.

        Refuses any flow requesting more than 40 shots (runaway guard).

        SAFETY — live-write hazard. flow_run drives REAL apps that can fire REAL
        network POSTs. A flow against an app with server-mutating screens must
        NEVER confirm a live-send action (e.g. `press select` on a "submit" /
        "send" screen or inside a value picker that commits on confirm). The
        convention is a ``# SAFETY RULES (live-write hazards — DO NOT TRIGGER):``
        comment block at the top of every flow file; the bundled example flows
        under ``pebble_mcp/examples/flows/`` show the pattern — follow their
        rules. Capturing those screens is fine; confirming them is not.
        """
        return run_flow_tool(flow_text, out_dir)

    @mcp.tool()
    def flow_validate(flow_text: str) -> dict[str, Any]:
        """Parse a flow spec WITHOUT running it — a cheap pre-check.

        Returns a step summary (step_count, shot_count, steps_by_type, and
        whether it exceeds the 40-shot flow_run cap) on success, or a clear
        parse error naming the offending 1-based line. Needs no emulator, so it
        is the right tool to validate a flow before spending an emulator run.
        """
        return validate_flow_tool(flow_text)

    @mcp.tool()
    def emu_screenshot(name: str | None = None) -> Image:
        """Capture one screenshot of the running emery emulator as an image.

        Optional ``name`` sets the file basename (``.png`` appended if absent).
        Errors clearly if no emulator is running or the emulator is wedged.
        """
        return screenshot_tool(name)

    @mcp.tool()
    def emu_input(
        action: str, button: str | None = None, duration_ms: int | None = None
    ) -> dict[str, Any]:
        """Send one input to the running emulator.

        ``action`` is 'press', 'longpress', or 'tap'. 'press'/'longpress'
        require ``button`` (one of back/up/select/down); 'longpress' also
        requires ``duration_ms`` (> 0). 'tap' fires an accelerometer tap and
        takes no button or duration. Button and action names are validated.
        """
        return input_tool(action, button, duration_ms)

    @mcp.tool()
    def emu_logs(
        seconds: float = 10.0,
        until_pattern: str | None = None,
        max_bytes: int = 65536,
    ) -> dict[str, Any]:
        """Capture emulator logs, bounded — never a firehose.

        Streams ``pebble logs`` for at most ``seconds``, optionally cutting at
        the first line containing ``until_pattern`` (``matched``), and clamps the
        result to the last ``max_bytes`` bytes (``truncated``).
        """
        return logs_tool(seconds, until_pattern, max_bytes)
