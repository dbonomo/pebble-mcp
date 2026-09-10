# pebble-mcp — Requirements & Landscape

Drafted 2026-07-18. The first MCP server for the Pebble ecosystem: appstore
search, the full local dev loop (build → emulator → screenshot flows), design
tooling for the 64-color world, and eventually one-command publish — usable
from any MCP host (Claude Code, Claude Desktop, claude.ai, other agents),
not just a shell.

## Why us, why now

- **The niche is empty.** Research (2026-07) found zero Pebble MCP servers —
  official or community. Core Devices is loudly agent-friendly (official
  Claude/Codex skill, `pebble publish` with auto-screenshots) but everything
  they ship assumes a shell. MCP is the missing portability layer.
- **We already own the hard parts.** `tools/gallery.py` is a working
  flow-driven emulator capture engine with wedge recovery; DESIGN.md encodes
  the palette/typography knowledge; the vendored `assets/` palettes are the
  ground truth for color tooling. The MCP server is largely a packaging job
  around proven pieces.
- **Community moment.** Contest culture + revival energy means a well-made
  `pebble-mcp` gets noticed and adopted.

## Landscape (what exists, what we wrap, what we skip)

| Thing | Status | Our relationship to it |
|---|---|---|
| `appstore-api.repebble.com` | Live, open read endpoints | **Wrap** — core of Tier 1 |
| `pebble-tool` CLI (uv) | Live, SDK 4.9.x | **Wrap** — build/emulator/logs/publish |
| Our `tools/gallery.py` | Working | **Absorb** — flow engine becomes a library + MCP tool |
| Official Claude skill (`coredevices/pebble-watchface-agent-skill`) | Live | **Complement, don't compete** — skill = knowledge/workflow, MCP = typed tools. The skill could *call* our server. |
| `pebble publish` (SDK 4.9.148+) | Live | **Wrap** (Tier 4, auth-gated) |
| Rebble Timeline API (`timeline-api.rebble.io/v1`) | Live | **Wrap** (Tier 4) — push/delete pins |
| CloudPebble (`coredevices/cloudpebble`, web IDE) | Live | **Skip as dependency; watch as target.** It's an interactive IDE, wrong shape to wrap. But its existence proves demand for shell-less Pebble dev — exactly our pitch. A future CloudPebble-hosted MCP bridge is their move, not ours. |
| Dev portal (`dev-portal.rebble.io`) | Live | Skip — browser flows; `pebble publish` covers the agent path |
| Bobby / on-watch AI, Dictation API | Live | Out of scope — app-side patterns, not dev tooling |
| SDK docs (`developer.repebble.com`, no llms.txt) | Live | **Partial** — expose curated doc excerpts as MCP *resources* (fonts table, GColor table, message-key rules). Not a docs mirror. |

## Tool surface (proposed)

### Tier 1 — Appstore (pure HTTPS, no auth, works in ANY host)
- `store_search(query, hardware?, type?, sort?, limit?)` — search apps/faces;
  default `hardware=emery`.
- `store_app(id)` — full metadata: hearts, screenshots, versions, companions.
- `store_collection(slug, type)` — e.g. `most-loved` watchfaces, home rows.
- `store_category(slug, ...)` / `store_developer(dev_id)`.
- `store_compare(ids[])` — bulk fetch + tabulate (hearts, features from
  descriptions) for competitive research.

### Tier 2 — Design toolkit (pure Python + Pillow, no toolchain needed)
- `color_nearest(hex_or_rgb)` — nearest of the 64 GColors: name, hex, C
  constant, sunlight-corrected preview value; contrast ratio vs a background.
- `palette_swatch(colors[])` — render a labeled swatch PNG for eyeballing.
- `image_quantize(png, dither?, corrected?)` — quantize any image to the
  64-color palette (uses vendored `.act`/`.aseprite` truth); returns preview +
  stats (colors used, out-of-gamut deltas).
- `image_prep(png, target?)` — resize/letterbox for emery 200×228 (or menu
  icon / appstore banner sizes), quantize, emit both 1× and 2× previews.
- `font_plan(text_or_glyphs, style?)` — recommend system fonts (LECO vs
  Gothic tables from DESIGN.md) for a given role/size; for custom fonts,
  compute the minimal `characterRegex` from the actual glyph set (the TTMM
  trick: ship only ~10 glyphs).
- `pdc_convert(svg)` — SVG → PDC vector via the svg2pdc pipeline, with
  the known constraints (even coords, flat paths) validated and reported.

### Tier 3 — Dev loop (requires local pebble-tool; degrade gracefully)
- `pebble_build(project_dir)` — build, return structured errors/warnings.
- `emu_start(platform=emery)` / `emu_stop(wipe?)`.
- `pebble_install(project_dir)` — bakes the kill+wipe lesson in.
- `emu_screenshot(name?)` — PNG back as MCP image content.
- `emu_input(press|longpress|tap, button?, duration?)`.
- `emu_logs(seconds|until_pattern)` — bounded log capture (never a firehose).
- `flow_run(flow_text | flow_path)` — the crown jewel: run a gallery.py flow
  spec, return every shot + the contact sheet. Flows carry the same
  live-POST safety-comment convention we established.

### Tier 4 — Authenticated (env-config tokens; OFF by default)
- `store_heart(id, on)` / `store_me()` — bearer token.
- `pebble_publish(project_dir, changelog?)` — wraps the new CLI publish with
  its auto-screenshot generation. Requires explicit config opt-in AND a
  per-call confirmation phrase — an agent must never accidentally ship.
- `timeline_push_pin(pin_json)` / `timeline_delete_pin(id)` — sandbox vs
  production keys from config.

### MCP resources (read-only reference, cheap wins)
- `pebble://colors` — the 64-color table (name, hex, corrected hex, roles).
- `pebble://fonts` — system font keys + size/style guidance.
- `pebble://platforms` — per-platform display specs (emery 200×228 etc.).
- `pebble://wire-conventions` — our delimited wire-format pattern write-up
  (generic version, coach specifics stripped).

## Architecture

- **Python 3.13, official `mcp` SDK (FastMCP), stdio transport** (HTTP later
  if someone wants to host it). One package: `pebble_mcp/`.
- **Tiered capability detection at startup**: Tier 1–2 always on (pure
  Python + Pillow); Tier 3 tools registered only if `pebble` resolves on
  PATH; Tier 4 only with tokens in config. `capabilities()` tool reports
  what's live so agents can plan.
- **gallery.py refactor**: extract the flow parser/driver into
  `pebble_mcp/flow.py` as a library; `tools/gallery.py` becomes a thin CLI
  over it. One engine, two frontends — no divergence.
- **Safety stance**: read tools are free; anything that spends money, mutates
  a store, or fires network POSTs beyond the appstore GET is opt-in via
  config + explicit tool-call confirmation fields. Emulator tools only ever
  touch the emulator.
- **Structured errors everywhere**: build failures return file:line arrays,
  not raw waf spew; emulator wedges auto-recover once (kill+wipe) then
  report honestly.
- **Tests**: pytest; recorded appstore API fixtures (no live calls in CI);
  palette math golden tests; flow-engine tests against a stub `pebble` shim.
  CI = the ~15-line uv GitHub Action (also fills the ecosystem's missing
  "official action" gap — document it in the README as reusable).

## Non-goals (v1)

- Not a docs mirror, not a CloudPebble replacement, not firmware tooling.
- No store scraping beyond the public API. No auto-publish without the
  explicit confirmation handshake. No watchapp-side/AI-voice features.
- Windows support: untested/best-effort (we develop on Linux; macOS likely
  fine via uv).

## Success criteria

1. From claude.ai (no shell), a user can search the store, compare the top
   emery faces, and get a quantized 64-color mockup of their own art.
2. From Claude Code, `flow_run` reproduces our entire 17-shot gallery
   through MCP alone.
3. A stranger can `uvx pebble-mcp` (or `uv tool install`) + add 5 lines to
   their MCP config and have Tier 1–2 working in under 2 minutes.
4. README demo GIF + announcement post ready for the rePebble forum.
