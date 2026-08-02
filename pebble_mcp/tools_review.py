"""Review / test tier MCP tools: ``design_review`` and ``project_smoke_test``.

Thin wrappers over :mod:`pebble_mcp.review`. All analysis / build / install /
screenshot logic lives in that library (and the modules it reuses — palette,
images, devloop); this module only adapts it to the MCP surface (string/dict in,
compact critique + MCP image content out) and owns the gating messages.

Both impl functions take an injectable ``runner``/``sleep`` (the shared
:data:`~pebble_mcp.flow.PebbleRunner` seam) so tests drive them with a stub
``pebble`` and never touch a real emulator.

Image returns follow the SDK-1.28 pattern used across this server: a tool
registered with ``structured_output=False`` may return ``[dict, Image, ...]`` and
FastMCP flattens it into one JSON text block plus one image block each (see
``tools_flow`` for the full write-up).
"""

from __future__ import annotations

import os
import tempfile
import time
from collections.abc import Callable
from typing import Any

from mcp.server.fastmcp import FastMCP, Image
from mcp.server.fastmcp.exceptions import ToolError

from . import devloop, images, review
from .capabilities import pillow_available
from .flow import PebbleRunner, _is_png, looks_wedged, subprocess_runner
from .tools_flow import repo_root

# Sentinels a caller can pass as ``source`` to mean "grab the live emulator
# screen" instead of a path / base64 payload.
_EMULATOR_SOURCES = frozenset({"emulator", "emu", "screen", "screenshot", "live"})

_MAX_PREVIEW_UPSCALE = 512


def _resolve(path: str) -> str:
    if "\x00" in path:
        raise ToolError("path contains a null byte, which is never valid")
    return path if os.path.isabs(path) else os.path.join(repo_root(), path)


def _require_pillow() -> None:
    if not pillow_available():
        raise ToolError(
            "design_review needs Pillow to read and analyze the image, which is "
            "not available here. Install it with `uv pip install pillow` and "
            "restart the server (see capabilities() -> tier2_design)."
        )


def _require_emulator() -> None:
    if not devloop.pebble_available():
        raise ToolError(
            "capturing from the running emulator needs the 'pebble' CLI, which "
            "was not found on PATH. Install it with `uv tool install "
            "pebble-tool` (ensure ~/.local/bin is on PATH), or pass a screenshot "
            "path / base64 / data-URI as `source` instead."
        )


def _capture_emulator(runner: PebbleRunner) -> str:
    """Grab one screenshot of the running emulator; return its file path."""
    _require_emulator()
    dest = os.path.join(tempfile.mkdtemp(prefix="pebble-review-"), "review.png")
    rc, out = runner(["pebble", "screenshot", "--no-open", dest], None, 90)
    if rc != 0 or not os.path.exists(dest) or os.path.getsize(dest) == 0 or not _is_png(dest):
        hint = (
            " The emulator looks wedged; try emu_stop(wipe=True) then reinstall."
            if looks_wedged(out)
            else " Is an emulator running? Install an app (which boots it) first."
        )
        raise ToolError(f"emulator screenshot failed (rc={rc}): {out[-200:].strip()}.{hint}")
    return dest


def _preview_image(img: images.Image.Image) -> Image:
    """PNG-encode a preview, 2×-upscaling small images for visibility."""
    if max(img.size) < _MAX_PREVIEW_UPSCALE:
        img = images.preview_2x(img)
    return Image(data=images.to_png_bytes(img), format="png")


# --------------------------------------------------------------------------- #
# design_review
# --------------------------------------------------------------------------- #
def design_review_impl(
    source: str,
    regions: int = review.DEFAULT_REGIONS,
    *,
    runner: PebbleRunner = subprocess_runner,
) -> list[Any]:
    """Implementation behind ``design_review`` (see ``register`` for docs)."""
    _require_pillow()
    if isinstance(source, str) and source.strip().lower() in _EMULATOR_SOURCES:
        path = _capture_emulator(runner)
        img = images.load_image(path)
    else:
        try:
            img = images.load_image(source)
        except ValueError as e:
            raise ToolError(
                f"could not read the screenshot: {e}. Pass a filesystem path, "
                "base64, a 'data:' URI, or the literal 'emulator' to capture the "
                "running screen."
            ) from e
    result = review.analyze(img, regions=regions)
    return [result.to_dict(), _preview_image(img.convert("RGB"))]


# --------------------------------------------------------------------------- #
# project_smoke_test
# --------------------------------------------------------------------------- #
def smoke_test_impl(
    project_dir: str,
    platform: str = "emery",
    *,
    runner: PebbleRunner = subprocess_runner,
    sleep: Callable[[float], None] = time.sleep,
) -> list[Any]:
    """Implementation behind ``project_smoke_test`` (see ``register`` for docs)."""
    try:
        result = review.smoke_test(
            _resolve(project_dir), platform, runner=runner, sleep=sleep
        )
    except devloop.PebbleUnavailableError as e:
        raise ToolError(str(e)) from e
    status = result.to_dict()
    if result.launch_shot:
        return [status, Image(path=result.launch_shot)]
    return [status]


# --------------------------------------------------------------------------- #
# Registration
# --------------------------------------------------------------------------- #
def register(mcp: FastMCP) -> None:
    """Attach the review / test tools to ``mcp``."""

    @mcp.tool(structured_output=False)
    def design_review(source: str, regions: int = review.DEFAULT_REGIONS) -> list[Any]:
        """Critique a Pebble screenshot against the design system.

        Analyzes one screenshot and returns an actionable, compact critique:
        palette adherence (what fraction of pixels are exact GColors, and the
        most-common off-palette colors with their nearest GColor), per-region
        contrast/legibility judged against the sunlight-corrected display values
        (the reflective LCD reads duller than sRGB), role guidance vs the
        `pebble://colors` house roles, and a prioritized suggestion list.

        `source` is a filesystem path, base64, or a `data:` URI of the image —
        OR the literal 'emulator' to capture and analyze the running emery
        screen (needs the `pebble` CLI; see capabilities()). `regions` sets the
        N×N contrast grid (default 3).

        This is a heuristic, not a human designer: it reasons about color
        statistics, never glyphs — it flags pairs that *look* illegible and
        colors that are off-palette or role-inappropriate; confirm the hero-value
        hierarchy by eye.

        Returns a JSON critique block plus the analyzed image.
        """
        return design_review_impl(source, regions)

    @mcp.tool(structured_output=False)
    def project_smoke_test(dir: str, platform: str = "emery") -> list[Any]:
        """Build, install, and screenshot a project — the one-call "does it run?".

        Builds `dir` (relative to the workspace root, or absolute); if it builds,
        installs the fresh .pbw onto the emulator (kill+wipe-first) and captures
        the launch screen. Returns {built, installed, errors[], warnings[],
        pbw_path, launch_shot, notes} plus the launch screenshot as an image.

        A failed build returns built=false with the compiler errors and skips
        install — so this is also the fastest "did my change compile" check.
        Requires the `pebble` CLI on PATH (see capabilities()).
        """
        return smoke_test_impl(dir, platform)
