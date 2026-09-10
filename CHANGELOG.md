# Changelog

All notable changes to `pebble-mcp` are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

Nothing yet.

## [0.2.0] — 2026-09-10

Grows the server from 23 tools to **29** (439 → 454 tests): real catalog-wide
store search, project scaffolding, and a review/test tier.

### Added

- **Catalog-wide `store_search`.** The appstore REST API has no text-search
  route (every `/api/v1/apps/search*` path returns the store front end's 404
  page), so `store_search` now queries the same hosted search index the store
  *website* uses — a public, search-only key lifted from its JS bundle. This is
  a genuine server-side search over the whole catalog: it finds low-heart and
  long-tail apps the old approach could not see. The previous client-side scan
  of the `all` / `most-loved` collections survives only as an automatic fallback
  when the index is unreachable, and the return says which path ran
  (`method`, plus `index_error` on the degraded path).
- **Phase 4 — project scaffolding (Workstream P):** `project_new`,
  `project_info`, `project_add_resource`, `project_set_meta`. Scaffolds a
  *buildable* watchface or watchapp in C or JavaScript (Alloy), across all
  seven platforms, with a fresh UUID — pure file generation, so it works in any
  host with no `pebble` CLI installed.
- **Phase 4 — review and test (Workstream R):** `design_review` (captures or
  accepts a screenshot and critiques it against the 64-color palette and the
  contrast/role guidance in `pebble://colors`) and `project_smoke_test`
  (build → install → screenshot flow → pass/fail, the one-call "does my app
  run" check).
- **`pebble://touch-interaction` resource** — swipe/tap handling on emery and
  gabbro, and how touch coexists with the buttons.
- `TOOL-SURFACE.md`, the usability contract every tool is held to, plus
  `PHASE-4.md`, `USABILITY-FINDINGS.md` and the roadmap/requirements docs.
- CI (lint + test on every push and PR) and a tag-triggered PyPI release
  workflow using Trusted Publishing (OIDC — no stored API token).

### Changed

- `pebble://platforms` now covers **all seven** platforms (aplite, basalt,
  chalk, diorite, emery, flint, gabbro) with correct display specs; it
  previously carried only emery/basalt/chalk/diorite. Platform identity is
  pinned: emery = Pebble Time 2, gabbro = Pebble Round 2.
- `__version__` is read from installed distribution metadata rather than
  hard-coded, so the package, `capabilities()` and the wheel cannot drift.
- Demo GIF in the README is referenced by absolute raw URL so it renders on
  PyPI as well as GitHub.

### Fixed

- **`mcp` is now pinned `>=1.9.0,<2`, which unbreaks fresh installs.** 0.1.0
  shipped an unbounded `mcp>=1.9.0`; mcp 2.x has since released, renaming
  `FastMCP` to `MCPServer` and moving `mcp.server.fastmcp`. A `uvx pebble-mcp`
  today resolves 2.x and dies at import with `ModuleNotFoundError: No module
  named 'mcp.server.fastmcp'` before the first tool call. Verified by
  installing the 0.2.0 wheel into a clean 3.13 venv: 29 tools list correctly.
- Devloop tests no longer require a real `pebble` CLI on `PATH` (they drive a
  stub runner), which unbreaks CI on a clean runner.
- Corrected the `.pbw` size comment in `tools_store`: the 32 MiB download cap
  was justified with a "real .pbw files are well under a megabyte" claim that
  is not true of fat multi-platform builds. The cap is unchanged; the reasoning
  is now honest.

### Known limitations

- `store_search`'s fallback listing scan re-fetches its candidate pool on every
  call — there is no cache across calls. Only reached when the search index is
  down, so the cost is rare; deferred rather than fixed because doing it
  properly means a per-client TTL cache plus a `refresh` argument and
  `fetched_at` in the return (TOOL-SURFACE §3), which is a tool-surface change,
  not a one-liner.
- Tier 4 (authenticated) is still detection-only: `capabilities()` reports it,
  but no authed tool ships yet. `pebble_publish` and the timeline pin tools are
  the whole planned surface there — `store_heart`/`store_me` were dropped
  2026-09-10.

## [0.1.0] — 2026-08-01

First public release. 23 tools across three live tiers, plus MCP resources.

### Added

- **Tier 1 — Appstore** (pure HTTPS, no auth, always on): `store_search`,
  `store_app`, `store_collection`, `store_category`, `store_developer`,
  `store_compare`, `store_download_pbw`. Upstream quirks are encoded rather
  than rediscovered — `sort=hearts` is silently ignored by the collection API,
  so hearts ordering is done client-side; `Page.total` is absent, so returns
  expose `has_more` instead.
- **Tier 2 — Design toolkit** (Python + Pillow): `color_nearest`,
  `palette_swatch`, `image_quantize`, `image_prep`, `font_plan`, `pdc_convert`
  — the 64-color GColor palette from the vendored `.act`/`.aseprite` truth,
  contrast math, dithered quantization, and SVG → PDC conversion with the
  known constraints validated.
- **Tier 3 — Dev loop** (registers only when the `pebble` CLI is on `PATH`):
  `pebble_build`, `pebble_install`, `emu_start`, `emu_stop`, `emu_screenshot`,
  `emu_input`, `emu_logs`, `flow_validate`, and `flow_run` — run a flow spec
  and get every screenshot back as an MCP image block *and* a saved file path.
  Sharp edges stay inside the tools: `pebble_install` bakes in kill+wipe,
  `flow_run` bakes in wedge recovery, log capture is bounded in bytes and
  seconds.
- **`capabilities()`** — cheap, per-call probe reporting which of the four
  tiers are live, so an agent can plan instead of calling a tool that is not
  wired up.
- **MCP resources:** `pebble://colors`, `pebble://fonts`, `pebble://platforms`,
  `pebble://wire-conventions`.
- `uvx pebble-mcp` packaging, MIT license, README with quickstart for each MCP
  host and a `flow_run` demo GIF.

<!-- 0.1.0 shipped to PyPI without a git tag, so it is linked by commit. -->
[Unreleased]: https://github.com/dbonomo/pebble-mcp/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/dbonomo/pebble-mcp/compare/5cd15dc...v0.2.0
[0.1.0]: https://github.com/dbonomo/pebble-mcp/commit/5cd15dc
