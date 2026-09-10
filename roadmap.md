# pebble-mcp — Roadmap

Drafted 2026-07-18. Companion to `pebble-mcp-requirements.md`. Execution
model: **Fable manages and integrates; Opus/Sonnet agents do all build work**,
in parallel wherever the dependency graph allows. No Fable subagents, ever.

Contest note: the Spring 2026 contest closed April 19 (winners drawn
April 28); no new contest is announced as of mid-July. So there is **no
external deadline** — we build for the community-launch moment and to be
ready the day a Summer/Fall contest drops. Core Devices floated monthly
developer hangouts; a live `pebble-mcp` demo there is our launch venue.

## Phase 0 — Foundation (sequential, short)

| # | Task | Agent | Depends on |
|---|---|---|---|
| 0.1 | Scaffold `pebble-mcp/` Python package: uv project, FastMCP hello-world server with `capabilities()` tool, pytest, ruff, GitHub Action (uv install → test) | Sonnet | — |
| 0.2 | Extract `tools/gallery.py` flow engine into `pebble_mcp/flow.py` library; gallery.py becomes a thin CLI over it; re-run the 17-shot gallery to prove no regression | Opus | 0.1 |

Gate (Fable): review package layout + flow-library API before fan-out. This
is the one moment everything is sequential — keep it under a day.

## Phase 1 — Parallel fan-out (three independent workstreams)

### Workstream A — Appstore client (Tier 1)
| # | Task | Agent |
|---|---|---|
| A1 | HTTP client for `appstore-api.repebble.com` with recorded fixtures; map every read endpoint | Sonnet |
| A2 | MCP tools: `store_search`, `store_app`, `store_collection`, `store_category`, `store_developer`, `store_compare` | Opus |
| A3 | Acceptance: reproduce the "top-50 most-loved emery faces feature tabulation" from research via tools alone | Sonnet |

### Workstream B — Design toolkit (Tier 2)
| # | Task | Agent |
|---|---|---|
| B1 | Palette core: load vendored `.act`/`.aseprite` truth; `color_nearest`, contrast math, `palette_swatch`; golden tests | Opus |
| B2 | Image pipeline: `image_quantize` (dither options, corrected/uncorrected), `image_prep` (emery/menu-icon/banner targets, 1×/2× previews) | Opus |
| B3 | `font_plan` (system font tables from DESIGN.md + minimal `characterRegex` computation) and `pdc_convert` (svg2pdc wrap + constraint validation) | Sonnet |
| B4 | MCP resources: `pebble://colors`, `pebble://fonts`, `pebble://platforms`, `pebble://wire-conventions` | Sonnet |

### Workstream C — Dev loop (Tier 3)
| # | Task | Agent |
|---|---|---|
| C1 | pebble-tool wrapper: `pebble_build` (structured errors), `emu_start/stop`, `pebble_install` (kill+wipe baked in), PATH-detection gating | Opus |
| C2 | `emu_screenshot`, `emu_input`, `emu_logs` (bounded capture) | Sonnet |
| C3 | `flow_run` on the Phase-0 flow library; returns shots as MCP image content + contact sheet | Opus |
| C4 | Acceptance: full 17-shot gallery reproduced through MCP calls only (from a Claude Code session with the server configured) | Sonnet |

Parallelism: A, B, C share nothing but the Phase-0 scaffold. Within streams,
A1→A2→A3 and C1→(C2,C3)→C4 are ordered; B1→B2 ordered, B3/B4 free.
Peak fleet: ~5 agents at once. Fable reviews each stream's PR-sized chunks.

## Phase 2 — Integration + hardening (after any two streams land)

| # | Task | Agent |
|---|---|---|
| 2.1 | Cross-host testing: Claude Code (stdio), Claude Desktop, claude.ai connector; capability degradation matrix (no pebble-tool, no Pillow extras) | Opus |
| 2.2 | Error-message audit: every failure path returns something an agent can act on | Sonnet |
| 2.3 | Adversarial review pass: one Opus agent per stream tries to break/misuse the tools (huge images, garbage flows, wedged emulator, malformed store responses) | Opus ×3 |

## Phase 3 — Auth tier + release (careful, mostly serial)

| # | Task | Agent |
|---|---|---|
| 3.1 | Config system (env/TOML): tokens, opt-in flags; `store_heart`, `store_me` | Sonnet |
| 3.2 | `pebble_publish` with the confirmation handshake; `timeline_push_pin`/`delete` (sandbox first) | Opus |
| 3.3 | Packaging: `uvx pebble-mcp` path, PyPI release, versioning | Sonnet |
| 3.4 | README with demo GIF (flow_run capturing a face), quickstart for each MCP host, the reusable GitHub Action documented | Opus |
| 3.5 | Launch: rePebble forum post + GitHub release; pitch to Core Devices (skill could reference the server) | Fable drafts, Dan approves & posts |

Publish/timeline tools touch Dan's real accounts — Fable reviews 3.2 line by
line; nothing in Phase 3 ships without Dan's explicit go.

## Standing rules

- Every agent task lands with tests; CI green before Fable review.
- Agents never call live authed endpoints; fixtures/sandbox only. Emulator
  work follows the flow-file safety-comment convention.
- Requirements doc is the contract — scope changes go through Fable + Dan,
  not agent improvisation.
- Definition of done for v1 = the four success criteria in
  `pebble-mcp-requirements.md`.
