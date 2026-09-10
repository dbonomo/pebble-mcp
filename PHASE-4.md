# Phase 4 — The ultimate dev companion

> **STATUS (2026-08-02) — server at 29 tools / 439 tests, pushed public.**
> - ✅ **P — scaffolding** (project_new/info/add_resource/set_meta, 7 platforms) — merged & wired.
> - ✅ **R — review/test** (design_review, project_smoke_test) — merged & wired.
> - ⏭️ **NEXT: U — publish** (store_generate_assets, then pebble_publish behind a
>   confirmation handshake). Tier-4 auth needs a Rebble token + Dan's explicit go.
> - 🔻 **LATER: Zig** (scaffolding + zig-pebble-sdk maintainer partnership) — do not pull forward.
> - Loose ends: 0.1.1 release (fixes PyPI dzbonomo URL + demo GIF); two store LOWs
>   (stale "<1MB" comment; store_search has no candidate-pool cache across calls).

Goal: pebble-mcp covers the **whole lifecycle**, not just the middle. Today it
can find, download, test, and design. It can't yet *create*, *review*, or
*publish*. Phase 4 closes that loop. Vision (Dan, 2026-08): support the full
matrix — **watchface or app × C / JavaScript / (explore: Zig, Rust) × every
platform (Pebble Time 2 = emery, Round, and the rest)** — and keep exploring.

## Pillar 1 — Create & manage (Workstream P) — the front door

Pure file generation, so it works in any host (no `pebble` CLI needed to
scaffold; only to build). Canonical structure comes from `pebble new-project`
(package.json with a `pebble` key: displayName, uuid, sdkVersion,
targetPlatforms, watchapp.watchface, messageKeys, resources.media) + wscript +
src templates.

- **`project_new(name, kind, language, platforms, companion, config, dest)`**
  — scaffold a *buildable* project.
  - `kind`: `watchface` | `watchapp` (sets `watchapp.watchface`).
  - `language`: `c` (src/c + wscript) | `javascript` (Rocky.js / Pure-JS
    structure — verify the modern shape). Zig/Rust: see the explore track.
  - `platforms`: any of aplite/basalt/chalk/diorite/emery/flint/gabbro; default
    a sensible modern set. Time 2 = emery; confirm the Round 2 platform id.
  - `companion`: add `src/pkjs/index.js` (phone-side JS for a C app).
  - `config`: add a Clay config page.
  - Generates a fresh UUID; returns path, uuid, files, and next steps.
- **`project_info(dir)`** — parse package.json: uuid, name, kind, language,
  platforms, capabilities, messageKeys, resources, sdkVersion.
- **`project_add_resource(dir, source, name, kind)`** — add an image/font,
  wire it into `resources.media`; images auto-prepped/quantized via the design
  tier.
- **`project_set_meta(dir, ...)`** — set/generate uuid, platforms,
  capabilities, messageKeys.
- Completeness: extend `pebble://platforms` to all seven platforms with correct
  display specs (currently only emery/basalt/chalk/diorite).

## Pillar 2 — Review, test, design (Workstream R)

- **`design_review(source, ...)`** — capture (or accept) a screenshot and
  analyze it against the design system: off-palette colors, per-region
  contrast/legibility vs the `pebble://colors` role guidance, actionable
  critique. Combines flow/emu + palette + images.
- **`project_smoke_test(dir, platform)`** — build → install → default
  screenshot flow → pass/fail + shots. The one-call "does my app run" check.

## Pillar 3 — Publish (Workstream U, Tier 4 auth — after launch, needs a token)

- **`store_generate_assets(project|screenshots)`** — required appstore banner
  (720×320) + per-platform screenshots at correct sizes.
- **`pebble_publish(dir, changelog)`** — auth-gated, confirmation handshake.

## Language support — the verdict (researched 2026-08)

`project_new(language=...)` support, honest tiers:

- **C** — first-class. The only fully official, fully documented, all-platform
  path.
- **JavaScript = Alloy** — first-class, and this is the *current* JS story
  (Moddable XS engine, on-watch modern JS/TS; Feb 2026 official launch), NOT
  Rocky.js. Structure: `src/embeddedjs/main.js` + `src/pkjs/index.js` +
  `src/c/mdbl.c`. **Alloy only targets emery (Time 2) + gabbro (Round 2)** —
  restrict/warn accordingly. Rocky.js is legacy (offer as a fallback for the
  older platforms only). PebbleKit JS is phone-side companion only.
- **Zig** — experimental, real: `github.com/vsergeev/zig-pebble-sdk` (solo
  maintainer, wraps the official SDK, builds .pbw for emery/gabbro). Offer
  later as clearly-labeled opt-in third-party, not first-class.
- **C++** — experimental, real: `github.com/codaris/pebble-cpp` (header-only
  wrapper over the C API). Low-risk follow-up option.
- **Rust** — dead. All efforts are 2015–2019 and abandoned; reviving means
  building an SDK-linking layer from scratch. Don't offer it; flag unsupported.
- **TinyGo/Nim/etc.** — nonexistent.

Platform identity confirmed: **emery = Pebble Time 2, gabbro = Pebble Round 2**.

ABI note (why non-C is hard): the app binary is raw Cortex-M Thumb-2 with a
firmware jump-table calling convention + PebbleProcessInfo header + load-time
relocation table, understood mostly via community reverse-engineering. Both
Zig and C++ sidestep this by wrapping the official SDK's link step — the only
proven approach for a third language.

## Cross-cutting play

- **CloudPebble API** — the durable move from the emulator research: open an
  issue/PR with Core Devices for a documented cloud build+emulator+screenshot
  REST API. That single endpoint set is what makes Tier 3 toolchain-free.

## Sequencing

Priority order (Dan, 2026-08 — build the core enhancers first, Zig last):

1. **P — project scaffolding (C + JavaScript/Alloy)** — the front door, first.
2. **R — review/test (design_review, project_smoke_test)** — in parallel with P.
3. **U — publish (store assets, pebble_publish)** — after launch (needs auth).
4. **Zig / experimental languages + the vsergeev partnership** — LAST, only
   after the C/JS build enhancers and review/publish have landed. Includes:
   `project_new(language="zig")` scaffolding against `zig-pebble-sdk`, a Zig
   build probe, a proven starter example, an onboarding recipe, and reaching
   out to the maintainer (as an on-ramp that sends them users — proof-build
   first, then a light GitHub-issue/discussion touch). C++ (`pebble-cpp`) is a
   lower-priority sibling. Do NOT pull these forward.

Each workstream ships self-contained `register(mcp)` modules; the main loop
does the `server.py` wiring after merge.

## Repo model (decided 2026-08)

Develop in the monorepo copy (`pebble-coach/pebble-mcp`) where the worktree
fleet + tooling live; the standalone public repo (`~/Development/pebble-mcp`,
github.com/dbonomo/pebble-mcp) is the **release mirror** — sync changed files
to it and push at each checkpoint. New releases go out from there via the
tag-triggered trusted-publishing workflow.
