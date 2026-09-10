# Tool-surface audit — 29 → 16

**Status: PROPOSAL. No tool code changed.** Written 2026-09-10 against the
0.2.0 tree. Judged by `TOOL-SURFACE.md` (the usability contract) and by Dan's
2026-09-10 steer in `docs/REORG-PLAN.md` Part 2: the MCP serves four verbs for
watch faces and apps — **find/copy-down, create, iterate, publish**. Index ring
and switchboard are out of scope.

The contract says **≤ 15 tools**. We shipped 0.2.0 at **29**. This proposes a
concrete path to **16 live tools**, growing to **18** when the publish tier
lands — one over the letter of the contract, and the reasoning for the overage
is in §2.4.

---

## 1. Every current tool, audited

Return sizes are measured where a fixture existed, estimated otherwise.
"Verb" is which of the four the tool actually serves for a caller — not which
one it is filed under.

| # | Tool | Module | Purpose | Verb | Typical return | Verdict |
|---|---|---|---|---|---|---|
| 1 | `capabilities` | `server.py` | Report which of the four tiers are live | *(meta — none)* | ~120 B | **merge into `doctor`** |
| 2 | `store_search` | `tools_store` | Catalog-wide title/keyword search via the store's hosted index | find | ~150 B/row × ≤50 | **keep** (becomes the one listing tool) |
| 3 | `store_app` | `tools_store` | Full metadata for one app by 24-hex id | find | ~2.0 KB | **keep** (absorbs `store_compare`) |
| 4 | `store_collection` | `tools_store` | List a named collection (`most-loved`, `all`, …) | find | ~150 B/row × 20 | **merge into `store_search`** |
| 5 | `store_category` | `tools_store` | List a category (`faces`, `games`, …) | find | ~150 B/row × 20 | **merge into `store_search`** |
| 6 | `store_developer` | `tools_store` | List one developer's apps | find | ~150 B/row × 20 | **merge into `store_search`** |
| 7 | `store_compare` | `tools_store` | Bulk-fetch ids into a comparison table | find | ~250 B/row | **merge into `store_app`** |
| 8 | `store_download_pbw` | `tools_store` | Fetch an app's latest `.pbw` to disk by id | find/copy-down | ~200 B + file | **keep** |
| 9 | `image_quantize` | `tools_design` | Quantize to the 64-color palette | create | ~1 KB + image | **merge into `image_prep`** |
| 10 | `image_prep` | `tools_design` | Resize/letterbox to a target, then quantize | create | ~1 KB + image | **keep** (absorbs #9) |
| 11 | `color_nearest` | `tools_design` | Nearest GColor + contrast vs a background | create | ~300 B | **merge into `palette_match`** |
| 12 | `palette_swatch` | `tools_design` | Render a labeled swatch PNG | create | image only | **merge into `palette_match`** |
| 13 | `font_plan` | `tools_fonts` | Recommend system fonts; compute minimal `characterRegex` | create | ~840 B | **keep** |
| 14 | `pdc_convert` | `tools_fonts` | SVG → Pebble Draw Command, constraints validated | create | ~1 KB + base64 | **keep** (lowest-traffic survivor) |
| 15 | `flow_run` | `tools_flow` | Run a flow spec; return every shot as an MCP image | iterate | ~1 KB + N images | **keep** (absorbs `flow_validate`) |
| 16 | `flow_validate` | `tools_flow` | Parse-only pre-check of a flow | iterate | ~200 B | **merge into `flow_run`** (`dry_run=`) |
| 17 | `emu_screenshot` | `tools_flow` | One-off screenshot of the running emulator | iterate | image only | **merge into `emu_drive`** |
| 18 | `emu_input` | `tools_flow` | press / longpress / tap a button | iterate | ~120 B | **merge into `emu_drive`** |
| 19 | `emu_logs` | `tools_flow` | Bounded log capture (64 KB / N seconds) | iterate | ≤64 KB, clamped | **keep** |
| 20 | `pebble_build` | `tools_devloop` | Build a project; structured file:line diagnostics | iterate | ~500 B–4 KB | **merge into `pebble_run`** |
| 21 | `pebble_install` | `tools_devloop` | Install a dir or `.pbw` (kill+wipe baked in) | iterate | ~400 B | **merge into `pebble_run`** |
| 22 | `emu_start` | `tools_devloop` | Boot the emulator | *(none — see §1.1)* | ~200 B | **cut** → `emu_drive(action="reset")` |
| 23 | `emu_stop` | `tools_devloop` | Shut down / wipe the emulator | *(none — recovery only)* | ~200 B | **cut** → `emu_drive(action="reset")` |
| 24 | `project_new` | `tools_project` | Scaffold a buildable watchface/app | create | ~1 KB | **keep** (gains `template=`) |
| 25 | `project_info` | `tools_project` | Parse `package.json` into a summary | create | ~600 B | **keep** |
| 26 | `project_add_resource` | `tools_project` | Copy a resource in, register it in `resources.media` | create | ~400 B | **merge into `project_edit`** |
| 27 | `project_set_meta` | `tools_project` | Edit the `pebble` metadata block in place | create | ~400 B | **merge into `project_edit`** |
| 28 | `design_review` | `tools_review` | Critique a screenshot against the design system | iterate | ~2 KB + image | **keep** |
| 29 | `project_smoke_test` | `tools_review` | build → install → launch screenshot, pass/fail | iterate | ~600 B + image | **merge into `pebble_run`** |

### 1.1 Why `emu_start` / `emu_stop` score "no verb"

Neither is something a caller wants; both are things the *emulator* needs.
`pebble_install` already kills and wipes before installing (TOOL-SURFACE §5),
and `flow_run` boots the emulator as a side effect of its `app`/`pbw` steps —
so no normal path ever calls `emu_start`. `emu_stop` survives only because
five error strings in the codebase tell the agent to run `emu_stop(wipe=True)`
when things wedge. That is a *recovery* affordance, and it should be named as
one.

---

## 2. Proposed target surface — 16 tools

Grouped by verb. "Absorbs" lists the current tools that disappear into it.

### 2.0 Meta

**`doctor()`** — absorbs `capabilities`.

No args. Returns the four tier flags exactly as `capabilities()` does today,
**plus**, for every tier that is off, the reason and the literal command to fix
it on the caller's OS (`uv tool install pebble-tool`, `uv pip install pillow`,
`export PEBBLE_API_TOKEN=…`), plus a `renamed_tools` map (see §4). This is
strictly a superset of `capabilities()`: the whole point of the tier probe is
"what can I do and what do I do about it", and today it answers only the first
half — every gate error has to re-embed its own install hint by hand.

### 2.1 Find / copy-down

**`store_search(query="", scope="search", slug=None, type_string="any", hardware="emery", limit=20, offset=0)`**
— absorbs `store_collection`, `store_category`, `store_developer`.

One listing tool. `scope` is an enum: `search` (the hosted index, the default),
`collection`, `category`, `developer`. `slug` carries the collection slug,
category slug, or developer id when `scope` is not `search`. Rows stay the
compact `_summary` projection; `has_more`/`offset` stay. This is exactly the
counter-example TOOL-SURFACE §1 warns about (`store_faces`/`store_apps` should
be one tool with a `type` arg) applied one level up — four tools that differ
only in which upstream path they hit and are otherwise byte-identical in shape.

**`store_app(ids: list[str])`** — absorbs `store_compare`.

One id returns the full metadata block (~2 KB). Two or more return the compact
comparison table `store_compare` returns today, with `missing_ids`. Same input
type, same upstream bulk endpoint; the only difference was arity, which is
precisely what §1 says should be an argument rather than a second tool.

**`store_download_pbw(app_id, dest_dir)`** — unchanged.

Kept separate from `store_app` deliberately: folding a network download into a
tool named "app" hides a side effect behind a read-shaped name. Later gains
`unpack: bool = false` for the `store_clone` idea in REORG-PLAN Part 2 — an
argument, not a fifteenth tool.

### 2.2 Create

**`project_new(name, kind="watchface", language="c", template=None, platforms=None, companion=False, config=False, dest_dir=".")`**

Unchanged plus the planned `template=` (`hail-watch`, `coach-watchface`,
`bikeface`) from REORG-PLAN Part 2.

**`project_info(dir)`** — unchanged. Read-only inspection; kept out of
`project_edit` because a tool named `_edit` should never be the way you read.

**`project_edit(dir, resource=None, uuid=None, add_platforms=None, remove_platforms=None, capabilities=None, message_keys=None)`**
— absorbs `project_add_resource`, `project_set_meta`.

Both current tools do the same thing: open `package.json`, mutate it, write it
back (one of them also copies a file in first). `resource` is an object
`{source, name, type, prep_target}`. Returns the updated `pebble` block plus
`files_written`. **Caveat, stated honestly:** this is the weakest merge in the
proposal — it produces a seven-optional-argument tool, which flirts with the
grab-bag smell §2 warns about. It is worth it because the alternative is two
tools with identical read-modify-write semantics, but if Dan dislikes it, this
is the one to reject and keep the surface at 17.

**`image_prep(source, target="palette-only", fit="contain", dither="floyd-steinberg", corrected=False)`**
— absorbs `image_quantize`.

`image_prep` is already a strict superset: it resizes then calls the exact same
`images.quantize`. Adding `target="palette-only"` (the new default) makes
`image_prep(src)` behave identically to today's `image_quantize(src)`. Two tools
where one enum value does the job.

**`palette_match(colors: list[str], background=None, swatch=False)`**
— absorbs `color_nearest`, `palette_swatch`.

Returns one match row per color (name, hex, C constant, argb8, corrected hex,
distance; plus contrast ratio and legibility when `background` is given) and,
when `swatch=true`, the rendered PNG as a second content block. The server
already returns mixed `[dict, Image]` results in four places, so this is the
house pattern, not a new one. Note the name: `palette` alone is legal under §1
("`noun` scoped by prefix") but `palette_match` says what it does.

**`font_plan(role_or_text, size_hint=None, style=None)`** — unchanged.

**`pdc_convert(svg_text)`** — unchanged.

### 2.3 Iterate

**`pebble_run(target, platform="emery", stop_after="shot")`**
— absorbs `pebble_build`, `pebble_install`, `project_smoke_test`.

The single most consequential merge. Today these three are nested subsets:
`pebble_build` ⊂ `pebble_install` (which builds when given a dir) ⊂
`project_smoke_test` (which builds, installs, and screenshots). One tool with
an enum that says where to stop:

- `stop_after="build"` — compile only, no emulator. Today's `pebble_build`,
  and the fastest "did my change compile" check.
- `stop_after="install"` — today's `pebble_install`.
- `stop_after="shot"` *(default)* — today's `project_smoke_test`. Defaults
  encode expertise (§2): the obviously-right thing on a one-argument call is
  the whole loop.

`target` is a project directory or a prebuilt `.pbw` (a `.pbw` makes the build
stage a documented no-op, so `store_download_pbw` → `pebble_run` stays a
two-call copy-down-and-see path). Returns `{built, installed, errors[],
warnings[], pbw_path, launch_shot, notes}` plus the screenshot.

**`emu_drive(action, button=None, duration_ms=None, shot=True)`**
— absorbs `emu_screenshot`, `emu_input`, `emu_start`, `emu_stop`.

`action` is an enum: `look`, `press`, `longpress`, `tap`, `reset`. `shot=true`
(the default) returns the resulting screen as an image, so the extremely common
press-then-look is **one** call, not two. `reset` is kill + wipe + reboot — the
single recovery affordance that replaces `emu_start`/`emu_stop`, and the thing
every "the emulator looks wedged" hint should point at.

This is not a switchboard: `emu_input` is *already* an action-enum tool with
conditionally-valid `button`/`duration_ms`, so this extends an existing,
tested pattern rather than inventing one, and §2 explicitly wants enum-ish
arguments to be real enums.

**Rejected here:** the suggestion that `emu_screenshot`/`emu_input` become
`flow_run` one-liners. They serve a different job. `flow_run` is for
*scripted, repeatable* capture — you know the sequence in advance, you want the
contact sheet. `emu_drive` is for *exploratory* iteration — the agent is
looking at a screen and deciding what to press next, and does not know the
sequence. Forcing that through `flow_run` costs a flow parse, a temp directory,
and knowledge of the flow DSL for what should be one call. Merging them into
`emu_drive` keeps the ergonomics and still removes two tools.

**`emu_logs(seconds=10.0, until_pattern=None, max_bytes=65536)`** — unchanged.
The only debugging surface; different return shape and different timing
semantics from anything else.

**`flow_run(flow_text, out_dir=None, dry_run=False)`** — absorbs
`flow_validate`.

Same input, same parse, same error messages. `dry_run=true` is exactly what
"validate" means: parse, report `step_count`/`shot_count`/`steps_by_type` and
whether it exceeds the 40-shot cap, touch no emulator.

**`design_review(source, regions=3)`** — unchanged.

### 2.4 Publish (planned — Workstream U)

**`store_generate_assets(...)`** and **`pebble_publish(dir, changelog, confirm)`**
per PHASE-4 Pillar 3, Tier-4 auth, two-step handshake. They are the whole
authed surface (`store_heart`/`store_me` were dropped 2026-09-10).

### 2.5 The count, honestly

**16 live tools** (§2.0–§2.3), **18** once publish lands. TOOL-SURFACE says
≤15. The proposal is one over, and the overage is `pdc_convert` — the one
survivor whose traffic I cannot honestly defend, since most Pebble faces use
bitmaps, not vectors. **If Dan wants the contract met to the letter, cut
`pdc_convert`** and document SVG→PDC as an out-of-band script. I have kept it
because an agent genuinely cannot do that conversion itself, and it is already
built and tested. That is a judgment call, not arithmetic — flagging it rather
than hiding it.

I did not manufacture further cuts to reach 12. Every remaining tool is the
sole front door to a capability an agent cannot reach another way.

---

## 3. The first five minutes

A fresh agent, no Pebble knowledge, only this server configured, opens the tool
list and sees 16 names in five obvious families: `doctor`, `store_*`,
`project_*`, `image_/palette_/font_/pdc_`, `pebble_run`/`emu_*`/`flow_run`/
`design_review`. Under the current 29, that same list has four near-identical
`store_*` listing tools, two image tools that both quantize, three overlapping
build tools, and four emulator-lifecycle tools — the shape of the surface
actively misleads about how many distinct things it can do.

Call one is `doctor()`: which tiers are live, and the literal command for each
one that is not.

Then, ≤2 calls per verb:

| Verb | Call 1 | Call 2 |
|---|---|---|
| **Find / copy-down** | `store_search("climbing", type_string="faces")` → ranked ids | `store_download_pbw(id, "./work")` → `.pbw` on disk |
| *(…and see it run)* | `store_download_pbw(id, "./work")` | `pebble_run("./work/x.pbw")` → installed + launch screenshot |
| **Create** | `project_new("Summit", template="coach-watchface")` → path, uuid, next steps | `pebble_run(path)` → builds, installs, screenshot |
| **Iterate** | `pebble_run(dir)` → compile diagnostics + launch shot | `design_review("emulator")` → palette/contrast critique |
| *(exploratory)* | `emu_drive("press", "down")` → the resulting screen | `emu_drive("press", "select")` → the next screen |
| **Publish** *(planned)* | `store_generate_assets(dir)` → banner + per-platform shots | `pebble_publish(dir, changelog, confirm=true)` |

Each of the four acceptance tests in TOOL-SURFACE §8 still lands in ≤2 calls,
and no call returns more than ~4 KB of text (the largest, `store_app` on a
single id, measures 1,963 bytes).

---

## 4. Migration notes

**Good news: the blast radius is almost nil.** A sweep of the whole
`pebble/` workspace outside the package found exactly **one** mechanical
caller-side reference to any pebble-mcp tool name:

- `/media/dbonomo/ssd2/Development/pebble/.claude/settings.local.json` — the
  permissions allowlist contains `"mcp__pebble-mcp__capabilities"`. Renaming
  `capabilities` → `doctor` makes that entry dead (it stops matching any tool
  — nothing crashes, the grant silently stops applying). **Update it in the
  same change.**

Two things that are *not* callers, contrary to the brief:

- **`pebble/.claude/skills/pebble-watchface/`** does not call this server at
  all. Every file was grepped: zero references to `pebble-mcp`, `pebble_mcp`,
  or any `mcp__pebble-mcp__*` tool. The skill drives the `pebble` CLI directly.
  Its `capabilities` hits are the unrelated Pebble app-manifest JSON field
  (`"capabilities": ["location"]`). **No migration needed.**
- **`pebble/tools/gallery.py`** has a *library* dependency, not a tool-name
  one: `from pebble_mcp.flow import AppStep, parse_flow_file, run_flow`. No MCP
  tool renames affect it. But it does mean `pebble_mcp.flow`'s public symbols
  are a compatibility surface in their own right — this audit proposes no
  changes there, and any future refactor of `flow.py` must not move those three
  names without updating `gallery.py`.

Everything else — `docs/REORG-PLAN.md`, `docs/DEVLOG.md`, the root `README.md`,
`refs/2048-touch/TOUCH-NOTES.md` — mentions tool names only in planning prose.
Safe to rename mechanically; the docs want a text pass afterward to stay
accurate.

### Renames that break a caller

| Old | New |
|---|---|
| `capabilities` | `doctor` |
| `store_collection`, `store_category`, `store_developer` | `store_search(scope=…, slug=…)` |
| `store_compare` | `store_app(ids=[…])` |
| `image_quantize` | `image_prep(target="palette-only")` |
| `color_nearest`, `palette_swatch` | `palette_match` |
| `pebble_build`, `pebble_install`, `project_smoke_test` | `pebble_run(stop_after=…)` |
| `emu_screenshot`, `emu_input`, `emu_start`, `emu_stop` | `emu_drive(action=…)` |
| `flow_validate` | `flow_run(dry_run=true)` |
| `project_add_resource`, `project_set_meta` | `project_edit` |

Unchanged: `store_search`, `store_app`, `store_download_pbw`, `project_new`,
`project_info`, `image_prep`, `font_plan`, `pdc_convert`, `flow_run`,
`emu_logs`, `design_review`.

### Should we ship aliases for one release?

**Recommendation: no.** Registering 13 deprecated shim tools would put the
surface back at 29 for a release — defeating the entire purpose, and the agent
reading the tool list is the customer we are optimizing for. pebble-mcp has
exactly one prior PyPI release and one known in-workspace consumer, so the
external cost of a clean break is close to zero.

Instead: ship the rename as **0.3.0**, a breaking pre-1.0 minor; put the table
above in `CHANGELOG.md`; update the one `settings.local.json` entry in the same
commit; and have **`doctor()` return a `renamed_tools` map** so an agent (or a
human) that goes looking for `pebble_build` gets told where it went, at zero
cost to the tool list.

### The unglamorous part

Roughly a dozen error strings hard-code the old names — every gate error says
"see capabilities()", and five wedge hints say "try `emu_stop(wipe=True)`".
`grep -rn 'capabilities()\|emu_stop(wipe' pebble_mcp/` before declaring the
rename done; a rename that leaves the error messages pointing at tools that no
longer exist is worse than no rename, because §4 makes those messages the
product.

---

## 5. USABILITY-FINDINGS status, re-verified against the 0.2.0 source

Every HIGH and MED was checked against current code and, where behavioral, run
live. Not assumed — several items I expected to be closed are not.

### Still OPEN (5)

- **HIGH — `flow`: mid-flow failure destroys the partial `FlowResult`.**
  `FlowResult` (`flow.py:359`) still carries only
  `name/shots/retries/wedge_recoveries/restarted` — no `success`, no failed-step
  index, no reason. In `_Driver.run()` a second `Wedged` is re-raised bare
  (`flow.py:538`), discarding every shot captured so far; and a mid-run
  `FlowParseError` (e.g. "shot before any app step", `flow.py:510`) is not
  caught by `run()` at all, so it propagates raw. **This is the most valuable
  one to fix** — it is the crown-jewel tool's failure path, and the contract
  (§4) says a failure must return what happened plus the next move.
- **HIGH — `devloop`: `build()` leaks a raw `FileNotFoundError`.**
  `devloop.build()` (`devloop.py:189-217`) still does no directory check before
  shelling out. Reproduced: `build('/nonexistent/path')` raises an unhandled
  `FileNotFoundError` traceback from `subprocess.run`, never returning a
  `BuildResult`. `install()` already handles the analogous case correctly, so
  the fix is a four-line copy of an existing pattern.
- **MED — `palette`: 3-letter words parse as hex.** Confirmed live:
  `parse_color('bad')` → `(187, 170, 221)`. `palette.py:192-212` still expands
  any 3-character string via `ch*2` with no leading-`#` requirement.
- **MED — `palette`: garbage raises `TypeError`, not the documented
  `ValueError`.** Confirmed live: `nearest(None)` →
  `TypeError: cannot unpack non-iterable NoneType object`; `nearest(12345)` →
  the `int` equivalent. Neither message names the expected `#RRGGBB` / `(r,g,b)`
  forms.
- **MED — `store`: `sort` is a silent no-op.** Still a parameter on
  `store_collection` / `store_category` / `store_developer`
  (`tools_store.py:483-531`), passed straight through with no client-side
  re-sort. Verified live: `sort='popular'` and `sort='recent'` return identical
  id order. None of the three docstrings mention it. **This one resolves
  itself in the proposal:** all three tools merge into `store_search`, so
  `sort` should simply not be carried across — which is what the finding asked
  for ("drop it from the tool surface").

### PARTIAL (1)

- **HIGH — no end-to-end store→install path; redirect trap.** The download half
  landed: `store_download_pbw` (`tools_store.py:401-441`) checks status,
  refuses a 0-byte body, caps at 32 MiB, and sanitizes the write path;
  `urllib_transport` uses `urlopen`, which follows the 307→R2 redirect
  transparently. Two halves are still missing: (a) **no Zip-magic
  verification** anywhere — a non-PBW 200 response of plausible size is written
  and handed to install, which is the exact unactionable-failure mode the
  finding was about; (b) there is still no single call that goes from a store
  id to an installed app — you must chain `store_download_pbw` → `pebble_install`
  by hand. Under this proposal (b) becomes a documented two-call path
  (`store_download_pbw` → `pebble_run`), which I think is the right answer;
  (a) is a genuine ~3-line fix that should just be done.

### FIXED (2)

- **HIGH — `store`: App objects are a context bomb.** Closed. `_summary()`
  (`tools_store.py:65`) is the compact projection, `_full_app()` (`:94`) emits
  structured fields with no `raw`, and every list-shaped tool routes through
  `_page_dict()`. Measured: a search row is 152 bytes, a full app 1,963 —
  against ~14 KB for a raw App at the time of the finding.
- **MED — `store`: `Page` can't answer "how many / is there more?"** Closed at
  the tool layer, which is where it matters: `_page_dict()` emits
  `has_more`/`limit`/`count`, and the upstream clamp is surfaced honestly
  (`limit=10000` comes back as `limit: 100` with `has_more: true`). The base
  `Page` dataclass still lacks a `has_more` property — cosmetic, since no tool
  returns a bare `Page`.

**Net: 5 open, 1 partial, 2 fixed** — and the LOWs from the original doc were
not re-checked here.

---

## 6. Open questions for Dan

1. **16 or 15?** The contract says ≤15 and this lands at 16. Cutting
   `pdc_convert` meets it exactly. Keep it (built, tested, genuinely
   unreproducible by an agent) or cut it (few faces use vector icons)?
2. **`project_edit`'s seven optional arguments** — real simplification, or a
   grab-bag? Rejecting this one merge is cheap and leaves the surface at 17.
3. **Clean break at 0.3.0, or aliases?** I recommend the clean break (§4).
   That assumes nobody outside this workspace is depending on 0.1.0/0.2.0 tool
   names — you would know better than I do whether anyone has picked it up
   since the PyPI release.
4. **Split the package?** REORG-PLAN Part 2 raises it and this audit does not
   settle it: Tier 3 is 6 of the 16 tools and needs the Pebble SDK installed.
   `pebble-mcp` (pure: store + design + project, works on claude.ai with no
   shell) vs `pebble-mcp[dev]` is a real fork in the road, and it interacts with
   the surface count — the pure surface would be 10 tools, comfortably inside
   the contract.
5. **Order of work:** the audit's own recommendation is to fix the open
   `flow`/`devloop` HIGHs *before* the rename, since both touch failure paths
   that the rename will churn anyway. Publish (Workstream U) still wants your
   explicit go and a Rebble token regardless.
