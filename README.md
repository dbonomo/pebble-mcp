# pebble-mcp

<p align="center">
  <img src="https://raw.githubusercontent.com/dbonomo/pebble-mcp/master/docs/demo.gif" alt="pebble-mcp downloading a store app and screenshotting it in the emulator" width="200">
</p>

An [MCP](https://modelcontextprotocol.io) server for the Pebble smartwatch
ecosystem: appstore search, a 64-color design toolkit, and the local dev loop
(build → emulator → screenshot flows) exposed as typed tools usable from any
MCP host — Claude Code, Claude Desktop, claude.ai, or any other agent — not
just a shell. The demo above is a single `flow_run`: it installs a real
appstore app (Hubble) onto the emery emulator, drives the buttons, and captures
each screen — all through MCP.

## Install

Once published to PyPI, no checkout is needed — `uvx` runs it on demand. Add
this to your MCP host's config (e.g. a Claude Code `.mcp.json`):

```json
{
  "mcpServers": {
    "pebble-mcp": {
      "command": "uvx",
      "args": ["pebble-mcp"]
    }
  }
}
```

To run from a local checkout instead (development, or before the PyPI release):

```json
{
  "mcpServers": {
    "pebble-mcp": {
      "command": "uv",
      "args": ["run", "--directory", "/absolute/path/to/pebble-mcp", "pebble-mcp"]
    }
  }
}
```

### Quickstart (Claude Code)

```bash
# from PyPI (once published)
claude mcp add pebble-mcp -- uvx pebble-mcp

# or from a local checkout
claude mcp add pebble-mcp -- uv run --directory /absolute/path/to/pebble-mcp pebble-mcp
```

Then, in a session, ask the agent to call `capabilities()` to see which tiers
are live, or `store_search("weather")` to hit the appstore immediately.

## Capability tiers

The server registers tools in four tiers and probes the environment at startup,
so an agent only ever sees the tools it can actually run.

- **Tier 1 — Appstore** (pure HTTPS, no auth, always on):
  `store_search`, `store_app`, `store_collection`, `store_category`,
  `store_developer`, `store_compare`, `store_download_pbw`.
- **Tier 2 — Design toolkit** (pure Python + Pillow):
  `color_nearest`, `palette_swatch`, `image_quantize`, `image_prep`,
  `font_plan`, `pdc_convert`. Pillow is a core dependency, so this tier is
  normally always on; `capabilities()` still probes for it and degrades
  gracefully if it is somehow absent.
- **Tier 3 — Dev loop** (requires the `pebble` CLI on `PATH`):
  `pebble_build`, `pebble_install`, `emu_start`, `emu_stop`,
  `emu_screenshot`, `emu_input`, `emu_logs`, `flow_validate`, and the crown
  jewel `flow_run` — run a gallery-style flow spec and get every screenshot
  back as MCP images. These tools register only when `pebble` is found.
- **Tier 4 — Authenticated** (requires `PEBBLE_API_TOKEN`; off by default):
  reserved for token-gated, network-mutating operations (heart an app,
  publish, timeline pins). Detected by `capabilities()`; opt-in only.

Read-only reference is also exposed as MCP resources: `pebble://colors`,
`pebble://fonts`, `pebble://platforms`, `pebble://wire-conventions`.

### The `capabilities()` tier probe

Call the `capabilities()` tool first. It reports which tiers are live in the
running environment (`tier1_appstore`, `tier2_design`, `tier3_devloop`,
`tier4_auth`) so an agent can plan its work instead of calling a tool that
isn't wired up. Tier 1 is always true; the rest reflect Pillow, the `pebble`
CLI, and `PEBBLE_API_TOKEN` respectively.

## Development

```bash
uv sync            # install deps (+ dev group: pytest, ruff)
uv run pytest      # run the test suite
uv run ruff check  # lint
```

The `pebble_mcp/examples/flows/` directory holds sample flow specs (used by the
tests and as living documentation of the flow format, including the
`# SAFETY RULES (live-write hazards — DO NOT TRIGGER):` convention that flow
authors should follow for any app with server-mutating screens).

## License

MIT — see [LICENSE](LICENSE). Copyright (c) 2026 Daniel Bonomo.

---

*Not affiliated with, endorsed by, or sponsored by Core Devices or Rebble.
"Pebble" and related marks belong to their respective owners.*
