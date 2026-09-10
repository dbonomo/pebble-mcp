"""Tests for pebble_mcp.tools_review — the review/test-tier MCP tools.

No real emulator or Pillow-less environment is required: the ``pebble`` CLI is
stubbed via the injected-runner seam, and Pillow gating is exercised by
monkeypatching ``tools_review.pillow_available``.
"""

from __future__ import annotations

import base64
import io
import os

import pytest
from mcp.server.fastmcp import FastMCP, Image
from mcp.server.fastmcp.exceptions import ToolError
from PIL import Image as PILImage

from pebble_mcp import devloop, tools_review

from .test_review import WAF_ERROR, SmokeRunner, _no_sleep, _project


# --------------------------------------------------------------------------- #
# Test doubles / fixtures
# --------------------------------------------------------------------------- #
def _real_png_bytes() -> bytes:
    buf = io.BytesIO()
    PILImage.new("RGB", (40, 40), (0, 0, 0)).save(buf, format="PNG")
    return buf.getvalue()


class ShotRunner:
    """Stub runner that writes a real (decodable) PNG for any ``pebble screenshot``."""

    def __init__(self, ok: bool = True):
        self.ok = ok
        self.calls: list[list[str]] = []

    def __call__(self, cmd, cwd=None, timeout=120):
        self.calls.append(cmd)
        if cmd[:2] == ["pebble", "screenshot"] and self.ok:
            with open(cmd[-1], "wb") as f:
                f.write(_real_png_bytes())
            return 0, "ok"
        if cmd[:2] == ["pebble", "screenshot"]:
            return 1, "no emulator running"
        return 0, "ok"


@pytest.fixture
def with_pebble(monkeypatch):
    monkeypatch.setattr(devloop.shutil, "which", lambda _name: "/usr/bin/pebble")


def _png_path(tmp_path, colors=((0, 0, 0), (255, 255, 255))) -> str:
    img = PILImage.new("RGB", (40, 40), colors[0])
    img.paste(PILImage.new("RGB", (40, 12), colors[1]), (0, 14))
    p = str(tmp_path / "shot.png")
    img.save(p)
    return p


def _png_b64(colors=((0, 0, 0), (255, 255, 255))) -> str:
    """Bare base64 of a small PNG, engineered to contain no ``/``.

    ``images.load_image`` treats a short string containing an ``os.path.sep``
    (``/`` on POSIX) as a filesystem path, so a bare-base64 payload that happens
    to include ``/`` would be misread. We nudge the canvas width until the
    encoding is ``/``-free, giving deterministic bare-base64 coverage.
    """
    for w in range(40, 80):
        img = PILImage.new("RGB", (w, 40), colors[0])
        img.paste(PILImage.new("RGB", (w, 12), colors[1]), (0, 14))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        data = base64.b64encode(buf.getvalue()).decode()
        if "/" not in data:
            return data
    raise AssertionError("could not build a /-free base64 sample")


# --------------------------------------------------------------------------- #
# Registration
# --------------------------------------------------------------------------- #
async def test_register_adds_expected_tools():
    mcp = FastMCP("test")
    tools_review.register(mcp)
    names = {t.name for t in await mcp.list_tools()}
    assert names == {"design_review", "project_smoke_test"}


# --------------------------------------------------------------------------- #
# design_review — inputs
# --------------------------------------------------------------------------- #
def test_design_review_from_path_returns_critique_and_image(tmp_path):
    content = tools_review.design_review_impl(_png_path(tmp_path))
    assert isinstance(content[0], dict)
    assert set(content[0]) == {
        "summary",
        "size",
        "palette",
        "contrast",
        "roles",
        "suggestions",
    }
    assert content[0]["palette"]["clean"] is True  # black+white are on-palette
    assert isinstance(content[1], Image)
    assert content[1].to_image_content().type == "image"


def test_design_review_from_base64_works():
    content = tools_review.design_review_impl(_png_b64())
    assert content[0]["palette"]["clean"] is True
    assert content[0]["size"]["height"] == 40


def test_design_review_from_data_uri_works():
    content = tools_review.design_review_impl("data:image/png;base64," + _png_b64())
    assert isinstance(content[0], dict)
    assert isinstance(content[1], Image)


def test_design_review_bad_source_is_actionable_error():
    with pytest.raises(ToolError, match="could not read the screenshot"):
        tools_review.design_review_impl("/no/such/file.png")


def test_design_review_from_emulator_captures_and_analyzes(with_pebble):
    runner = ShotRunner()
    content = tools_review.design_review_impl("emulator", runner=runner)
    assert isinstance(content[0], dict)
    assert any(c[:2] == ["pebble", "screenshot"] for c in runner.calls)


def test_design_review_emulator_gated_without_pebble(monkeypatch):
    monkeypatch.setattr(devloop.shutil, "which", lambda _name: None)
    with pytest.raises(ToolError, match="pebble"):
        tools_review.design_review_impl("emulator", runner=ShotRunner())


def test_design_review_gated_without_pillow(monkeypatch, tmp_path):
    monkeypatch.setattr(tools_review, "pillow_available", lambda: False)
    with pytest.raises(ToolError, match="Pillow"):
        tools_review.design_review_impl(_png_path(tmp_path))


def test_design_review_off_palette_input_flags_it():
    # A gradient PNG (base64) reports off-palette colours through the tool.
    img = PILImage.new("RGB", (48, 48))
    for y in range(48):
        for x in range(48):
            img.putpixel((x, y), (x * 5 + 1, y * 5 + 1, 3))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    uri = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
    content = tools_review.design_review_impl(uri)
    assert content[0]["palette"]["clean"] is False
    assert content[0]["palette"]["off_palette"]


# --------------------------------------------------------------------------- #
# project_smoke_test
# --------------------------------------------------------------------------- #
def test_smoke_test_build_ok_returns_status_and_image(with_pebble, tmp_path):
    proj = _project(tmp_path)
    content = tools_review.smoke_test_impl(str(proj), runner=SmokeRunner(), sleep=_no_sleep)
    status = content[0]
    assert status["built"] is True
    assert status["installed"] is True
    assert status["launch_shot"] and os.path.exists(status["launch_shot"])
    assert isinstance(content[1], Image)


def test_smoke_test_build_fail_returns_errors_no_image(with_pebble, tmp_path):
    proj = _project(tmp_path)
    content = tools_review.smoke_test_impl(
        str(proj),
        runner=SmokeRunner(build_out=WAF_ERROR, build_rc=1),
        sleep=_no_sleep,
    )
    assert len(content) == 1  # no image block
    status = content[0]
    assert status["built"] is False
    assert status["installed"] is False
    assert status["errors"]


def test_smoke_test_gated_without_pebble(monkeypatch, tmp_path):
    monkeypatch.setattr(devloop.shutil, "which", lambda _name: None)
    with pytest.raises(ToolError, match="pebble"):
        tools_review.smoke_test_impl(str(tmp_path / "app"), runner=SmokeRunner())


async def test_smoke_test_docstring_names_the_gate():
    mcp = FastMCP("test")
    tools_review.register(mcp)
    smoke = next(t for t in await mcp.list_tools() if t.name == "project_smoke_test")
    assert "pebble" in smoke.description and "CLI" in smoke.description
