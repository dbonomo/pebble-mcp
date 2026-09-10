# pebble-mcp — Tool Surface Contract

The usability contract for every MCP tool this server exposes. The requirements
doc says *what* ships; this says *how it must feel*. A tool that violates this
doc is not done, even if it works. Scope changes go through Fable + Dan.

Guiding test for every design decision: **an agent that has never seen Pebble
before reads only the tool list and schemas — can it do the task on the first
call, and when it can't, does the error tell it what to do next?**

## 1. Small surface, strong verbs

- Target **≤ 15 tools total** across all tiers. Prefer one tool with a clear
  argument over two near-duplicate tools. (Counter-example to avoid: separate
  `store_faces` / `store_apps` — it's one `store_search` with a `type` arg.)
- Every tool name is `noun_verb` or `noun` scoped by prefix: `store_*`,
  `image_*`, `font_*`, `emu_*`, `pebble_*` (build/install), `flow_*`.
- No tool exists just to expose a library function. If an agent wouldn't reach
  for it while building or shipping a watchface, it doesn't get a tool.
  Library ≠ surface.

## 2. Descriptions and schemas are the product

- Tool description: first sentence = what it does for the caller. Then when to
  use it, then sharp edges. Written for a model, tuned like docs.
- Every enum-ish argument IS an enum in the schema (platforms, buttons,
  collection slugs, dither modes). Never "string, see docs".
- Defaults encode expertise: `hardware="emery"`, sane limits, safe modes. A
  zero-argument or one-argument call should do the obviously-right thing.
  (Lesson from GymTracker: value on first call, never "configure elsewhere
  first".)

## 3. Returns: compact, structured, honest

- **Context is the scarce resource.** List-shaped returns are trimmed
  projections (id, title, author, hearts, one-line summary) — never the raw
  App object. Full detail is a *by-id* lookup. Cap list returns (default
  ~10, max ~50) and always include pagination state the agent can act on:
  `{offset, returned, has_more}`.
- Screenshots and swatches return as **MCP image content blocks** (the agent
  sees them) *plus* the saved file path (the human keeps them). Both, always.
- Numbers the store API lies about get fixed in the tool: hearts sorting is
  client-side (`sort=hearts` is silently ignored upstream — measured, not
  assumed). The tool's output order is the order it promises.
- Anything cached carries `fetched_at`. Cached list tools accept
  `refresh: bool = false`. Stale-on-network-failure returns data + a
  `stale: true` flag, not an exception.

## 4. Errors an agent can act on

- Every failure path returns: what failed, why (upstream detail bounded to a
  few hundred chars), and **the next move** ("run capabilities()", "the id
  looks malformed — store ids are 24 hex chars", "emulator wedged; it was
  kill+wiped, retry your call").
- Tier-gated tools fail fast with the gate named: calling `pebble_build`
  without the CLI returns the install hint, not a traceback.
- No silent None. A lookup that finds nothing says so and echoes what it
  looked for.

## 5. Sharp edges stay inside the tool

- `pebble_install` bakes in kill+wipe. `flow_run` bakes in wedge recovery
  (one restart, then a structured failure). Agents never need to know the
  folklore; that's the whole point of the server.
- Emulator tools are serialized internally (one QEMU); a second concurrent
  call queues or fails clearly — it never interleaves.
- Log/output capture is always bounded (bytes and seconds) with the truncation
  stated in the return.

## 6. Safety and tiers

- Tier 4 (authed) tools: mutating calls (`pebble_publish` and the timeline pin
  pushes — the only two authed surfaces) require an explicit `confirm: true`
  argument and describe exactly what will happen when called without it.
  Publish is a two-step handshake, never one call.
  (`store_heart`/`store_me` dropped 2026-09-10 — serve none of the four verbs
  find/copy-down, create, iterate, publish; never implemented.)
- `capabilities()` stays cheap, evaluated per-call, and is the single source
  of truth the other tools' gate errors point at.
- Nothing phones home; the only network calls are the ones the tool name
  promises.

## 7. Known upstream quirks (encode, don't rediscover)

| Quirk | Tool-level answer |
|---|---|
| `sort=hearts` ignored by collection API | client-side sort, documented in description |
| collection type is `apps`/`faces` (not `watchapps`) | enum hides the slug entirely |
| `Page.total` absent | expose `has_more` from returned-vs-limit instead |
| REST API has no text-search route (every `/api/v1/apps/search*` path returns the store front-end's 404 page) | `store_search` queries the hosted search index the store *website* uses (Algolia, public search-only key from its JS bundle) — real catalog-wide search; the old listing scan survives only as an automatic fallback |
| search-index hits carry no `latest_release` | `store_search` rows have no `pbw_url`; the docstring routes you to `store_app`/`store_download_pbw` by id |
| watchapps need wipe-before-install to foreground | baked into `pebble_install` |

## 7b. By-id is a first-class flow

Search is not the only front door, and the tools say so. Every store listing
URL ends in the app's 24-hex id
(`https://apps.repebble.com/2048-touch_6df87b64b7174448a065ef54`), and
`store_app` / `store_download_pbw` / `store_compare` all take that id directly.
`store_app`'s and `store_download_pbw`'s descriptions state where an id comes
from, and `store_search` points at the by-id path whenever it returns nothing
(and always on the degraded scan path). An agent that has been handed a store
link should never need a search call.

## 8. Definition of slick (acceptance)

A fresh agent session with only this server configured can, unprompted:
1. name the top-5 most-loved emery faces (correct order, one call),
2. build a broken project and report the *actionable* compiler error,
3. run a 3-step flow and *show* the screenshots,
4. explain why a Tier-4 tool call didn't run and what's needed to enable it —
   each in ≤ 2 tool calls, no call returning > ~4 KB of text.
