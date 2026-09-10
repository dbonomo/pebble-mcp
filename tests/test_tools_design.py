"""Tests for the pebble_mcp.tools_design MCP tool wrappers (roadmap task B2).

Exercises the tools through FastMCP's own call path (register -> list_tools ->
call_tool) so the content-block return shape used in SDK 1.28 is verified, not
just the underlying library.
"""

from __future__ import annotations

import base64
import io
import json

import pytest
from mcp.server.fastmcp import FastMCP
from mcp.types import ImageContent, TextContent
from PIL import Image as PILImage

from pebble_mcp import tools_design


@pytest.fixture
def mcp() -> FastMCP:
    app = FastMCP("test-design")
    tools_design.register(app)
    return app


def _gradient(w: int = 16, h: int = 16) -> PILImage.Image:
    img = PILImage.new("RGB", (w, h))
    px = img.load()
    for y in range(h):
        for x in range(w):
            px[x, y] = (int(255 * x / (w - 1)), int(255 * y / (h - 1)), 64)
    return img


def _data_uri(img: PILImage.Image) -> str:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def _content(result) -> list:
    """FastMCP.call_tool returns content, or (content, structured) for structured
    tools; normalize to the content sequence."""
    if isinstance(result, tuple):
        return list(result[0])
    return list(result)


# --------------------------------------------------------------------------- #
# Registration
# --------------------------------------------------------------------------- #
async def test_registers_expected_tools(mcp):
    names = {t.name for t in await mcp.list_tools()}
    assert {"image_quantize", "image_prep", "color_nearest", "palette_swatch"} <= names


# --------------------------------------------------------------------------- #
# image_quantize
# --------------------------------------------------------------------------- #
async def test_image_quantize_returns_stats_and_image(mcp):
    content = _content(
        await mcp.call_tool("image_quantize", {"source": _data_uri(_gradient()), "dither": "none"})
    )
    kinds = [type(c) for c in content]
    assert TextContent in kinds and ImageContent in kinds
    stats = json.loads(next(c for c in content if isinstance(c, TextContent)).text)
    assert stats["dither"] == "none"
    assert stats["num_colors"] == len(stats["colors_used"])
    assert stats["colors_used"][0]["name"].startswith("GColor")
    img_block = next(c for c in content if isinstance(c, ImageContent))
    assert img_block.mimeType == "image/png"
    # The base64 image payload decodes to a real PNG.
    png = base64.b64decode(img_block.data)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"


async def test_image_quantize_corrected_flag(mcp):
    content = _content(
        await mcp.call_tool("image_quantize", {"source": _data_uri(_gradient()), "corrected": True})
    )
    stats = json.loads(next(c for c in content if isinstance(c, TextContent)).text)
    assert stats["corrected"] is True


async def test_image_quantize_accepts_bare_base64(mcp):
    buf = io.BytesIO()
    _gradient(8, 8).save(buf, format="PNG")
    bare = base64.b64encode(buf.getvalue()).decode()
    content = _content(await mcp.call_tool("image_quantize", {"source": bare}))
    assert any(isinstance(c, ImageContent) for c in content)


# --------------------------------------------------------------------------- #
# image_prep
# --------------------------------------------------------------------------- #
async def test_image_prep_reports_target_size(mcp):
    content = _content(
        await mcp.call_tool(
            "image_prep",
            {"source": _data_uri(_gradient(40, 40)), "target": "menu-icon"},
        )
    )
    stats = json.loads(next(c for c in content if isinstance(c, TextContent)).text)
    assert stats["target"] == "menu-icon"
    assert stats["target_size"] == {"width": 25, "height": 25}
    assert stats["fit"] == "contain"
    assert any(isinstance(c, ImageContent) for c in content)


async def test_image_prep_cover_fit(mcp):
    content = _content(
        await mcp.call_tool(
            "image_prep",
            {"source": _data_uri(_gradient()), "target": "emery", "fit": "cover"},
        )
    )
    stats = json.loads(next(c for c in content if isinstance(c, TextContent)).text)
    assert stats["fit"] == "cover"
    assert stats["target_size"] == {"width": 200, "height": 228}


# --------------------------------------------------------------------------- #
# color_nearest
# --------------------------------------------------------------------------- #
async def test_color_nearest_hex(mcp):
    result = await mcp.call_tool("color_nearest", {"color": "#01FE02"})
    content = _content(result)
    payload = json.loads(content[0].text)
    assert payload["name"] == "GColorGreen"
    assert payload["hex"] == "00FF00"
    assert payload["c_constant"] == "GColorGreen"
    assert payload["argb8"] == 0b11001100


async def test_color_nearest_rgb_triple(mcp):
    content = _content(await mcp.call_tool("color_nearest", {"color": "255, 0, 0"}))
    payload = json.loads(content[0].text)
    assert payload["name"] == "GColorRed"
    assert payload["exact"] is True


async def test_color_nearest_with_background_contrast(mcp):
    content = _content(
        await mcp.call_tool("color_nearest", {"color": "#FFFFFF", "background": "#000000"})
    )
    payload = json.loads(content[0].text)
    assert payload["contrast_ratio"] == 21.0
    assert payload["legible"] is True
    assert payload["background"] == "000000"


# --------------------------------------------------------------------------- #
# palette_swatch
# --------------------------------------------------------------------------- #
async def test_palette_swatch_returns_image(mcp):
    content = _content(
        await mcp.call_tool("palette_swatch", {"colors": ["#FF0000", "00FF00", "0,0,255"]})
    )
    assert len(content) == 1
    block = content[0]
    assert isinstance(block, ImageContent)
    assert block.mimeType == "image/png"
    png = base64.b64decode(block.data)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"


async def test_palette_swatch_with_labels(mcp):
    content = _content(
        await mcp.call_tool(
            "palette_swatch",
            {"colors": ["#FF0000", "#00FF00"], "labels": ["stop", "go"]},
        )
    )
    assert isinstance(content[0], ImageContent)
