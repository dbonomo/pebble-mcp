# pebble-mcp — Adversarial usability findings (2026-07-22)

58 probes against store/palette/devloop/flow at 025b2b1, judged by the
TOOL-SURFACE.md standard: "does the return/error give an agent what it needs
to act, compactly?" Burn these down before (or while) wiring the tool layer;
each HIGH blocks its module's tool from meeting the contract.

## HIGH

- [ ] **store: App objects are a context bomb.** One App ≈ 14 KB serialized
  (5.8 KB even without `raw`, which duplicates 31 keys); 100 apps ≈ 1.4 MB.
  Fix: compact `App.summary()` projection (id, title, type, author, hearts,
  uuid, pbw_url); list-shaped tools emit only that, never `raw` or URL lists.
- [ ] **devloop: `build()` leaks a raw `FileNotFoundError`** (traceback, not a
  result) when `project_dir` doesn't exist — contradicts the module's own
  no-raw-exceptions contract; `install()` already does this right for a bad
  pbw path. Fix: validate the dir, return
  `BuildResult(success=False, output="no such project directory: ...")`.
- [ ] **flow: any mid-flow failure raises bare `Wedged`/`FlowParseError` and
  destroys the partial `FlowResult`** — shots already captured, recovery
  counts, and the failing step index are all lost. Fix: always return a
  FlowResult with `success`, failed-step + reason, and shots-so-far;
  exceptions only for programmer errors.

## HIGH (found 2026-07-22, hands-on install of store id 4c848836…)

- [ ] **No end-to-end store→install path, and a redirect trap in the fetch.**
  `store.py` exposes `Release.pbw_url` but has **no PBW download method** — the
  flow the future `pebble_install(store_id=…)` needs doesn't exist. When it's
  built: the pbw URL 307-redirects to Cloudflare R2 (presigned, GET-only,
  ~2 h expiry). A fetch that doesn't follow redirects silently writes a
  **0-byte file** and install fails with an unactionable "App install failed."
  Fix: a `fetch_pbw()` that follows redirects, verifies non-zero length +
  Zip magic, and errors with the real cause; `pebble_install` wraps it.

## MED

- [ ] **palette: 3-letter words parse as hex.** `'bad'` → `#BBAADD`;
  `contrast_ratio('bad','#fff')` → 2.12, no error. Reject bare 3-char
  shorthand (or require leading `#`).
- [ ] **palette: garbage raises `TypeError`, not the documented `ValueError`**
  (`nearest(None)`, `nearest(12345)`), with messages that never name the
  expected `#RRGGBB`/(r,g,b) forms. Catch early in `parse_color`.
- [ ] **store: `sort` is a silent no-op** — 'popular' and 'recent' return
  identical id order from the live API. Drop it from the tool surface (tools
  sort client-side per TOOL-SURFACE §3) or hard-document the no-op.
- [ ] **store: `Page` can't answer "how many / is there more?"** — no
  `total`/`has_more`; `limit=10000` silently clamped to 100. Add explicit
  `has_more` (from `next_page_url`) + surface the clamp.

## LOW

- [ ] devloop: `BuildResult.output` unbounded (logs_capture clamps at 64 KB;
  build should tail-clamp too, with a truncated flag).
- [ ] devloop: non-pebble dir → `success=False` with **zero diagnostics**;
  surface "not a project directory" as a Diagnostic or `reason` field.
- [ ] flow: `shot` before `app` passes parse and only explodes mid-run —
  validate ordering at parse time.
- [ ] flow: zero-width unicode → confusing "unknown command"; negative
  `wait`/`longpress` durations accepted silently.
- [ ] palette: bools and floats silently coerced (`(True,False,True)` →
  (1,0,1); floats truncated). Reject bools; round or document truncation.

## Verified good (don't touch)

- palette hex-garbage errors ('not-a-color', '#GGGGGG', out-of-range ints) —
  clear ValueErrors; `get()`/`from_argb8()` None-for-non-palette is correct.
- devloop diagnostics on a real broken build: project-relative paths, correct
  error/warning split, did-you-mean preserved.
- `logs_capture` bounding is exemplary (64 KB clamp + truncated flag +
  `until_pattern` + timeout floor) — it's the template for the build clamp.
- flow parse errors carry precise 1-based line numbers.
- store 404/400 mapping and local `type_string` guard are clean.
