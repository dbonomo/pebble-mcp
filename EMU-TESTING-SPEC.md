# pebble-mcp — Emulator boot / manage / test-app tool spec

What it takes to launch and drive a Pebble app in the emery emulator, distilled
from doing it by hand across a full app build. This is the contract for the
Tier-3 dev-loop tools that let an agent boot the emulator, install apps, drive
them, and see the result. Honors TOOL-SURFACE.md.

> **STATUS — implemented (2026-08-01).** The wiring "gap" this spec describes is
> closed: all Tier-3 tools listed below are registered on the server
> (`pebble_build`, `pebble_install`, `emu_start`, `emu_stop`, `emu_screenshot`,
> `emu_input`, `emu_logs`, `flow_run`, `flow_validate`). The "Operational
> knowledge to bake in" folklore is now infrastructure: button-press pacing
> (`BUTTON_SETTLE_SEC`) and post-install settle (`POST_INSTALL_SETTLE_SEC`) are
> baked into the flow driver and `emu_input`, so correctness no longer depends
> on a flow author inserting waits. Kept as the design record. The two items
> still deliberately unimplemented — auto kill+wipe recovery on *standalone* emu
> tools (they surface a clear hint instead, to avoid destroying state the agent
> may want), and hard serialization of the single emulator — are noted inline.

## Current state (what exists vs. the gap)
- `devloop.py` LIBRARY already has: `build()`, `install()` (kill+wipe-first),
  `emu_start()`, `emu_stop(wipe=)`, `logs_capture()`, plus GCC-diagnostic parsing
  and WEDGE_MARKERS.
- `flow.py` LIBRARY already has the full driver: parse a flow spec, install, and
  step through button/tap/wait/screenshot with wedge recovery.
- MISSING in the library: `emu_screenshot()` and `emu_input()` (button/tap).
- MISSING on the server: NONE of these are registered as MCP tools yet — the
  server only exposes `capabilities()` + `pebble://platforms`. Wiring is the gap.

## The launch/test loop (what "launch an app" actually requires)
1. **Gate** on `capabilities().tier3_devloop` (pebble CLI on PATH, ~/.local/bin).
   If absent, fail fast with the install hint — never a traceback.
2. **Build** (optional): `pebble build` in a project dir → `build/<name>.pbw`.
   Return structured diagnostics (already implemented).
3. **Clean install**: `pebble kill` → `pebble wipe` → sleep ~3s →
   `pebble install --emulator emery <pbw>`. The kill+wipe BEFORE install is
   load-bearing — a persisted active app otherwise stays foreground and the new
   app never launches. (Already baked into `install()`.)
4. **Settle**: sleep ~6s after install for the app to fully render (some apps
   load resources / animate).
5. **Observe**: screenshot the current screen (native 200x228 PNG).
6. **Drive**: send button/tap input, screenshot again, repeat.

## Tools to add / wire (small surface, per TOOL-SURFACE.md)
- `pebble_build(project_dir)` — wire `build()`. Structured errors.
- `pebble_install(pbw_or_project)` — wire `install()` (kill+wipe baked in).
- `emu_start()` / `emu_stop(wipe?)` — wire existing.
- **`emu_screenshot()`** — NEW. `pebble screenshot --no-open <tmp>`; verify the
  file is non-empty; return as an **MCP image content block** (agent sees it)
  PLUS the saved path. This is the key tool for "testing".
- **`emu_input(action, button?, duration_ms?)`** — NEW. Wraps
  `pebble emu-button click <back|up|select|down>` (+ `--duration` for long-press)
  and `pebble emu-tap`. Validate button against the enum.
- `emu_logs(...)` — wire `logs_capture()` (bounded).
- **`flow_run(flow_or_project)`** — wire `flow.py`'s `run_flow()`: install + step
  a scripted flow, returning shots as image content + a contact sheet. This is
  the one-call "test an app" payoff (roadmap C3).

## Operational knowledge to BAKE IN (folklore → infrastructure)
- **Button-press pacing:** the emery emulator DROPS TO THE WATCHFACE / desyncs on
  rapid presses. `emu_input` must pace internally (~0.2-0.3s between presses) and
  flow_run must space steps — never fire presses back-to-back.
- **Wedge recovery:** output containing any WEDGE_MARKER ("not responding",
  "no emulator", "failed to connect", "connection refused", "timed out") → the
  emulator is wedged; run kill+wipe and retry the operation ONCE, then fail with
  a clear message. (flow.py already does this; emu tools should too.)
- **Screenshot capture is racy for animations:** a sub-second animation can't be
  reliably sampled through screenshot latency. For animated content, expose an
  optional per-frame step (or document capturing a slowed build) rather than
  promising to catch a transient.
- **Single emulator:** there is ONE QEMU instance. Serialize emulator-touching
  tools (queue or fail clearly); never let two installs/screenshots interleave.
- **Window vs headless:** launching surfaces a QEMU window on a local display
  when one is available (the user can interact directly), and still screenshots
  fine when headless. Don't assume either.
- **Timings:** ~3s after wipe, ~6s after install, before the first screenshot.

## Acceptance
A fresh agent with the server configured can, in ≤ a few calls:
1. build a project and report an actionable compile error,
2. install it and SEE the launch screen (image content),
3. press a button and see the screen change,
4. run a multi-step flow and get back the screenshots + contact sheet —
each degrading gracefully (named gate error) when the pebble CLI is absent.
