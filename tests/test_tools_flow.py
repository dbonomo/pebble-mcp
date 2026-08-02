"""Tests for pebble_mcp.tools_flow — the Tier-3 flow / emulator MCP tools.

No real emulator is ever touched: the ``pebble`` CLI is stubbed via the same
injected-runner seam the flow engine uses (the StubRunner pattern from
test_flow.py), and PATH-gating is satisfied by monkeypatching
``devloop.shutil.which`` so ``pebble_available()`` reports true.
"""

from __future__ import annotations

import pytest
from mcp.server.fastmcp import FastMCP, Image
from mcp.server.fastmcp.exceptions import ToolError

from pebble_mcp import devloop, tools_flow


# --------------------------------------------------------------------------- #
# Test doubles
# --------------------------------------------------------------------------- #
class StubRunner:
    """Records commands and returns queued (rc, out); writes stub PNGs for shots.

    Mirrors tests/test_flow.py's StubRunner: ``fail`` maps a command-substring
    to a fail count (rc=1 + wedge marker) before succeeding.
    """

    def __init__(self, fail: dict[str, int] | None = None, make_shot_files: bool = True):
        self.calls: list[list[str]] = []
        self.fail = dict(fail or {})
        self.make_shot_files = make_shot_files

    def __call__(self, cmd, cwd=None, timeout=120):
        self.calls.append(cmd)
        for marker, remaining in list(self.fail.items()):
            if remaining > 0 and any(marker in part for part in cmd):
                self.fail[marker] = remaining - 1
                return 1, f"error: {marker} not responding"
        if self.make_shot_files and cmd[:2] == ["pebble", "screenshot"]:
            with open(cmd[-1], "wb") as f:
                f.write(b"\x89PNG\r\n\x1a\n")
        return 0, "ok"

    def commands(self) -> list[str]:
        return [" ".join(c) for c in self.calls]


def _no_sleep(_seconds: float) -> None:
    pass


@pytest.fixture
def with_pebble(monkeypatch):
    """Make ``pebble_available()`` report true (pretend the CLI is on PATH)."""
    monkeypatch.setattr(devloop.shutil, "which", lambda _name: "/usr/bin/pebble")


# --------------------------------------------------------------------------- #
# Registration
# --------------------------------------------------------------------------- #
async def test_register_adds_expected_tools():
    mcp = FastMCP("test")
    tools_flow.register(mcp)
    names = {t.name for t in await mcp.list_tools()}
    assert names == {
        "flow_run",
        "flow_validate",
        "emu_screenshot",
        "emu_input",
        "emu_logs",
    }


async def test_flow_run_docstring_mentions_safety_convention():
    mcp = FastMCP("test")
    tools_flow.register(mcp)
    flow_run = next(t for t in await mcp.list_tools() if t.name == "flow_run")
    assert "SAFETY" in flow_run.description
    assert "examples/flows" in flow_run.description


# --------------------------------------------------------------------------- #
# flow_run — happy path returns metadata + images
# --------------------------------------------------------------------------- #
def test_flow_run_returns_metadata_and_images(with_pebble, tmp_path):
    runner = StubRunner()
    out = str(tmp_path / "shots")
    content = tools_flow.run_flow_tool(
        "app demo\nwait 1\nshot first\npress down\nshot second\n",
        out_dir=out,
        runner=runner,
        sleep=_no_sleep,
    )

    meta = content[0]
    assert isinstance(meta, dict)
    assert meta["flow"] == "flow"
    assert meta["shot_count"] == 2
    assert meta["retries"] == 0
    assert meta["wedge_recoveries"] == 0
    assert [s["filename"] for s in meta["shots"]] == ["01-first.png", "02-second.png"]

    images = content[1:]
    assert len(images) == 2
    assert all(isinstance(img, Image) for img in images)
    # Each image points at a real, written stub PNG on disk.
    for img, shot in zip(images, meta["shots"], strict=True):
        assert str(img.path) == shot["path"]
        assert img.path.exists()
        # Converts to protocol image content without error.
        assert img.to_image_content().type == "image"


def test_flow_run_reports_wedge_recovery(with_pebble, tmp_path):
    # First screenshot wedges -> engine kill+wipe and restarts the flow once.
    runner = StubRunner(fail={"screenshot": 1})
    content = tools_flow.run_flow_tool(
        "app demo\nwait 1\nshot only\n",
        out_dir=str(tmp_path / "s"),
        runner=runner,
        sleep=_no_sleep,
    )
    assert content[0]["wedge_recoveries"] == 1
    assert content[0]["restarted"] is True
    assert len(content) == 2  # metadata + one recovered shot


def test_flow_run_defaults_out_dir_to_tempdir(with_pebble):
    content = tools_flow.run_flow_tool(
        "app demo\nwait 1\nshot only\n", runner=StubRunner(), sleep=_no_sleep
    )
    out_dir = content[0]["out_dir"]
    assert out_dir and content[0]["shots"][0]["path"].startswith(out_dir)


# --------------------------------------------------------------------------- #
# flow_run — error surfacing
# --------------------------------------------------------------------------- #
def test_flow_run_parse_error_surfaces_line_number(with_pebble):
    with pytest.raises(ToolError) as exc:
        tools_flow.run_flow_tool(
            "app demo\nfrobnicate x\n", runner=StubRunner(), sleep=_no_sleep
        )
    assert "line 2" in str(exc.value)
    assert "unknown command" in str(exc.value)


def test_flow_run_refuses_over_shot_cap(with_pebble):
    lines = ["app demo"] + [f"shot s{i}" for i in range(tools_flow.MAX_SHOTS + 1)]
    with pytest.raises(ToolError) as exc:
        tools_flow.run_flow_tool(
            "\n".join(lines), runner=StubRunner(), sleep=_no_sleep
        )
    assert str(tools_flow.MAX_SHOTS) in str(exc.value)
    assert "cap" in str(exc.value)


def test_flow_run_at_cap_is_allowed(with_pebble, tmp_path):
    lines = ["app demo"] + [f"shot s{i}" for i in range(tools_flow.MAX_SHOTS)]
    content = tools_flow.run_flow_tool(
        "\n".join(lines), out_dir=str(tmp_path / "s"), runner=StubRunner(), sleep=_no_sleep
    )
    assert content[0]["shot_count"] == tools_flow.MAX_SHOTS


def test_flow_run_gated_without_pebble(monkeypatch):
    monkeypatch.setattr(devloop.shutil, "which", lambda _name: None)
    with pytest.raises(ToolError, match="pebble"):
        tools_flow.run_flow_tool("app demo\nshot s\n", runner=StubRunner())


# --------------------------------------------------------------------------- #
# flow_validate — parse-only, no emulator needed
# --------------------------------------------------------------------------- #
def test_flow_validate_returns_summary_without_pebble(monkeypatch):
    # Explicitly no pebble on PATH: flow_validate must still work (cheap check).
    monkeypatch.setattr(devloop.shutil, "which", lambda _name: None)
    summary = tools_flow.validate_flow_tool(
        "app demo\nwait 1\nshot a\npress down\nshot b\n"
    )
    assert summary["valid"] is True
    assert summary["shot_count"] == 2
    assert summary["step_count"] == 5
    assert summary["over_shot_cap"] is False
    assert summary["steps_by_type"]["ShotStep"] == 2


def test_flow_validate_parse_error():
    with pytest.raises(ToolError, match="line 1"):
        tools_flow.validate_flow_tool("press middle\n")


# --------------------------------------------------------------------------- #
# emu_screenshot
# --------------------------------------------------------------------------- #
def test_emu_screenshot_returns_image(with_pebble):
    img = tools_flow.screenshot_tool("myshot", runner=StubRunner())
    assert isinstance(img, Image)
    assert img.path.name == "myshot.png"
    assert img.path.exists()


def test_emu_screenshot_errors_when_no_emulator(with_pebble):
    # Runner reports a wedge marker and writes no file.
    runner = StubRunner(fail={"screenshot": 1})
    with pytest.raises(ToolError, match="screenshot failed"):
        tools_flow.screenshot_tool(runner=runner)


def test_emu_screenshot_rejects_non_png_output(with_pebble):
    # rc==0 and a non-empty file, but not a PNG -> still an error (not a bogus img).
    def runner(cmd, cwd=None, timeout=120):
        with open(cmd[-1], "wb") as f:
            f.write(b"GIF89a not a png")
        return 0, "ok"

    with pytest.raises(ToolError, match="screenshot failed"):
        tools_flow.screenshot_tool(runner=runner)


def test_emu_screenshot_gated_without_pebble(monkeypatch):
    monkeypatch.setattr(devloop.shutil, "which", lambda _name: None)
    with pytest.raises(ToolError, match="pebble"):
        tools_flow.screenshot_tool(runner=StubRunner())


# --------------------------------------------------------------------------- #
# emu_input — validation
# --------------------------------------------------------------------------- #
def test_emu_input_press_issues_click(with_pebble):
    runner = StubRunner()
    res = tools_flow.input_tool("press", "select", runner=runner)
    assert res["ok"] is True
    assert runner.commands() == ["pebble emu-button click select"]


def test_emu_input_longpress_issues_duration(with_pebble):
    runner = StubRunner()
    tools_flow.input_tool("longpress", "select", 600, runner=runner)
    assert runner.commands() == ["pebble emu-button click select --duration 600"]


def test_emu_input_tap(with_pebble):
    runner = StubRunner()
    tools_flow.input_tool("tap", runner=runner)
    assert runner.commands() == ["pebble emu-tap"]


def test_emu_input_invalid_action(with_pebble):
    with pytest.raises(ToolError, match="invalid action"):
        tools_flow.input_tool("wiggle", "select", runner=StubRunner())


def test_emu_input_invalid_button(with_pebble):
    with pytest.raises(ToolError, match="invalid button"):
        tools_flow.input_tool("press", "middle", runner=StubRunner())


def test_emu_input_longpress_requires_duration(with_pebble):
    with pytest.raises(ToolError, match="requires duration_ms"):
        tools_flow.input_tool("longpress", "select", runner=StubRunner())


def test_emu_input_press_rejects_duration(with_pebble):
    with pytest.raises(ToolError, match="does not take duration_ms"):
        tools_flow.input_tool("press", "select", 600, runner=StubRunner())


def test_emu_input_longpress_rejects_absurd_duration(with_pebble):
    with pytest.raises(ToolError, match="cap"):
        tools_flow.input_tool(
            "longpress", "select", tools_flow.MAX_DURATION_MS + 1, runner=StubRunner()
        )


def test_emu_input_longpress_at_cap_is_allowed(with_pebble):
    runner = StubRunner()
    res = tools_flow.input_tool(
        "longpress", "select", tools_flow.MAX_DURATION_MS, runner=runner
    )
    assert res["ok"] is True


def test_emu_input_tap_rejects_button(with_pebble):
    with pytest.raises(ToolError, match="no button"):
        tools_flow.input_tool("tap", "select", runner=StubRunner())


def test_emu_input_gated_without_pebble(monkeypatch):
    monkeypatch.setattr(devloop.shutil, "which", lambda _name: None)
    with pytest.raises(ToolError, match="pebble"):
        tools_flow.input_tool("press", "select", runner=StubRunner())


# --------------------------------------------------------------------------- #
# emu_logs — bounded passthrough
# --------------------------------------------------------------------------- #
def test_emu_logs_bounds_until_pattern_and_max_bytes(with_pebble):
    lines = "".join(f"line {i}\n" for i in range(100)) + "TARGET here\nafter\n"

    def runner(cmd, cwd=None, timeout=120):
        assert cmd[:2] == ["pebble", "logs"]
        return 0, lines

    res = tools_flow.logs_tool(seconds=5, until_pattern="TARGET", runner=runner)
    assert res["matched"] is True
    assert res["returncode"] == 0
    assert res["text"].rstrip().endswith("TARGET here")
    assert "after" not in res["text"]


def test_emu_logs_truncates_to_max_bytes(with_pebble):
    big = "x" * 5000 + "\n"

    def runner(cmd, cwd=None, timeout=120):
        return 0, big

    res = tools_flow.logs_tool(max_bytes=1000, runner=runner)
    assert res["truncated"] is True
    assert len(res["text"].encode("utf-8")) <= 1000


def test_emu_logs_gated_without_pebble(monkeypatch):
    monkeypatch.setattr(devloop.shutil, "which", lambda _name: None)
    with pytest.raises(ToolError, match="pebble"):
        tools_flow.logs_tool(runner=lambda *a, **k: (0, ""))
