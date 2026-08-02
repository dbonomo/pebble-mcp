"""pebble-mcp server entry point (stdio transport).

Tool tiers (see capabilities()): 1 appstore, 2 design, 3 dev loop,
4 authenticated (not yet wired). Each tier's tools live in their own
tools_*/resources module exposing register(mcp).
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from pebble_mcp import (
    resources,
    tools_design,
    tools_devloop,
    tools_flow,
    tools_fonts,
    tools_project,
    tools_review,
    tools_store,
)
from pebble_mcp.capabilities import get_capabilities

mcp = FastMCP("pebble-mcp")


@mcp.tool()
def capabilities() -> dict[str, object]:
    """Report which pebble-mcp capability tiers are available in this environment.

    tier1_appstore is always true (pure HTTPS). tier2_design requires Pillow.
    tier3_devloop requires the `pebble` CLI on PATH. tier4_auth requires
    PEBBLE_API_TOKEN to be set. Also reports the server version.
    """
    return get_capabilities()


tools_store.register(mcp)    # tier 1: store_* (search/browse/compare/download)
tools_design.register(mcp)   # tier 2: image_quantize/prep, color_nearest, palette_swatch
tools_fonts.register(mcp)    # tier 2: font_plan, pdc_convert
tools_flow.register(mcp)     # tier 3: flow_run/validate, emu screenshot/input/logs
tools_devloop.register(mcp)  # tier 3: pebble_build/install, emu start/stop
tools_project.register(mcp)  # phase 4: project_new/info/add_resource/set_meta
tools_review.register(mcp)   # phase 4: design_review, project_smoke_test
resources.register(mcp)      # pebble:// colors, fonts, platforms, wire-conventions


def main() -> None:
    """Start the pebble-mcp server on stdio."""
    mcp.run()


if __name__ == "__main__":
    main()
