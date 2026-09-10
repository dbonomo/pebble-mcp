# Launch checklist — get pebble-mcp on PyPI

Your follow-along working doc. The community page's TODO is the public
overview; **this** is the granular execution list. Check boxes as you go.

**Legend**
- 🟢 **Claude does it** — just say "go" and I'll do it (you review after).
- 🔵 **You do it** — needs your account, credentials, or a decision only you can make.
- ⏱ rough time.

**This weekend's goal: get fully publish-READY and kick the tires — not publish.**
Publishing (Steps 5–6) waits until you've tested enough to feel confident. So the
plan is: I make everything publish-ready *in place* (no repo carve-out yet, so your
testing environment stays intact), and you spend the weekend actually using it
(see "Kick the tires" below). Pull the trigger on PyPI whenever you're ready.

---

## Step 0 — Decisions · 🔵 · ✅ DONE

- [x] **License: MIT.**
- [x] **Package name: `pebble-mcp`** (confirmed available on PyPI).
- [x] **Authorship: Daniel Bonomo <d.z.bonomo@gmail.com>.**

---

## Kick the tires this weekend — get a feel for it · 🔵 · ⏱ as long as it's fun

Do this from your **second terminal** (the MCP-connected one). **Restart that
instance first** so it picks up all 23 tools — it connected when the server had
only one. Then just talk to Claude naturally; these are prompts to try, grouped by
tier. Jot down anything that feels wrong — that's exactly the feedback Phase 3 needs.

**Tier 1 · Store (works even with no emulator):**
- [ ] "Search the Pebble store for weather watchfaces that run on my Time 2."
- [ ] "Show me the most-loved watchfaces — which ones include weather?"
- [ ] "Compare Hubble against another astronomy app: hearts, platforms, size."
- [ ] "Download the Hubble pbw so we can try it."

**Tier 2 · Design (the fun, visual one — works anywhere, even claude.ai):**
- [ ] "What's the nearest Pebble color to #3b82f6? Is it legible on black?"
- [ ] "Quantize this image to the Pebble 64-color palette." *(drop in a logo/photo)*
- [ ] "Prep this image as a 25×25 launcher menu icon."
- [ ] "Make me a swatch of the coach app's accent colors."
- [ ] "Which font for a big clock numeral? Give me the characterRegex for `15:37`."
- [ ] "Convert this SVG to PDC." *(try one with a curve — watch it refuse honestly)*

**Tier 3 · Dev loop (uses your local `pebble` CLI):**
- [ ] "Run a screenshot flow on hail-watch and show me the frames."
- [ ] "Build coach-workout and tell me about any warnings."
- [ ] "Install Hubble in the emulator and screenshot the moon screen."
- [ ] "Screenshot whatever's on the emulator right now."

**Resources (reference data the model can pull):**
- [ ] "Read the pebble://colors resource — what are the role colors?"
- [ ] "Show me the wire-format conventions from pebble-mcp."

**Try to break it / find rough edges (this is the valuable part):**
- [ ] Give a vague request ("make my watchface art Pebble-ready") and see if Claude
      picks the right tool from the descriptions alone.
- [ ] Feed junk: a corrupt image, a nonsense color, a 10,000-character search.
- [ ] Note **any** moment Claude picks the wrong tool, an error message confuses
      you, or you expected a tool that doesn't exist. Add it to
      `pebble-mcp/TOOL-SURFACE.md` (your other terminal already started that doc).

---

## Step 0-decisions are done. The rest below is the ready-then-publish path.

---

## Step 1 — Make it publish-ready in place (decouple from the monorepo) · 🟢 · IN PROGRESS

Doing this now, *without* carving out a separate repo yet — so your testing
environment stays a single tree all weekend. The physical carve-out + GitHub push
becomes a mechanical step at publish time (Step 5b), because after this the
package has no monorepo coupling left.

- [ ] 🟢 **Break the monorepo coupling:** `tests/test_flow.py` reads the flow
      specs from `../tools/flows/`. Vendor those `.flow` files into the package
      (`pebble_mcp/examples/flows/`) and repoint the test — verified by running
      pytest on a copy of the package alone, outside the repo.
- [ ] 🟢 Genericize the last coach/hail mentions in `tools_flow.py` / `devloop.py`
      docstrings (public repo hygiene; safety wording stays).
- [ ] 🟢 Confirm no personal leaks (`btf4e`, `dbonomo`, `/media/`, `trainer`).

### Step 5b (deferred to publish day) — carve out & push · 🔵
- [ ] Create the empty **public** GitHub repo `pebble-mcp` (no README/license — we
      bring our own). Copy the now-self-contained `pebble-mcp/` dir into it; I'll
      give you the exact `git init` + `remote add` + `push`.

---

## Step 2 — Packaging metadata · 🟢 · ⏱ 20 min

- [ ] 🟢 Add the `LICENSE` file (once you've picked one in Step 0).
- [ ] 🟢 Fill out `pyproject.toml`: `description`, `authors`, `license`,
      `keywords`, `classifiers` (Python 3.13, MCP, License, OS), `readme`,
      `requires-python = ">=3.13"`, and `[project.urls]` (Homepage / Repository
      / Issues).
- [ ] 🟢 Verify the build: `uv build` produces an sdist + wheel, and
      `uvx --from ./dist/pebble_mcp-*.whl pebble-mcp` boots from the wheel alone.

---

## Step 3 — README · 🟢 · ⏱ 20 min

- [ ] 🟢 Write `README.md`: one-paragraph what-it-is, the four tiers, the `uvx`
      config snippet, a quickstart per host (Claude Code / Desktop), the
      `capabilities()` note, license, and the "not affiliated with Core
      Devices/Rebble" line.
- [ ] 🟢 Embed the demo GIF from Step 4 at the top.

---

## Step 4 — The demo GIF · 🟢 (needs the local emulator) · ⏱ 20 min

- [ ] 🟢 Run the Hubble store→screenshot flow through `flow_run` and assemble the
      captured frames into an animated GIF. **This is the single most convincing
      launch artifact** — it's what earns the retweet.
- [ ] 🟢 Drop it in the repo (`docs/demo.gif`) and wire it into the README + the
      community page.

---

## Step 5 — Release workflow, trusted publishing (no tokens) · 🟢 write / 🔵 configure · ⏱ 15 min

- [ ] 🟢 Add `.github/workflows/release.yml` — builds on a `v*` tag and publishes
      via `pypa/gh-action-pypi-publish` with `permissions: id-token: write` (OIDC,
      so **no API token to store**).
- [ ] 🔵 On PyPI: create an account if needed, verify email, **enable 2FA**.
- [ ] 🔵 On PyPI → your account → *Publishing* → **Add a pending publisher** with:
      PyPI project name `pebble-mcp`, owner `<your-github-user>`, repository
      `pebble-mcp`, workflow `release.yml`, environment (leave blank or `pypi`).
      *(This authorizes GitHub to publish without a password.)*

---

## Step 6 — Ship it · 🔵 tag / 🟢 verify · ⏱ 10 min

- [ ] 🔵 `git tag v0.1.0 && git push origin v0.1.0` — this triggers the workflow,
      which publishes to PyPI.
- [ ] 🟢 Verify the real thing: `uvx pebble-mcp` from a clean directory boots and
      `capabilities()` responds. (I'll walk the install as a stranger would.)
- [ ] 🟢 Flip the community page + config snippets from "not yet on PyPI / local
      checkout" to the live `uvx pebble-mcp` one-liner.

---

## Step 7 — Announce (in this order) · 🟢 draft / 🔵 post · ⏱ 30 min

- [ ] 🔵 **Rebble Showcase forum thread** (forum.rebble.io) — the durable home
      base. 🟢 I'll draft it (lead with the SVG→PDC + 64-color quantization hook).
- [ ] 🔵 **X, tagging @ericmigi** — highest leverage; he's publicly wanted exactly
      this. 🟢 I'll draft the post; the GIF is the payload.
- [ ] 🔵 **Discord `#app-dev`** (rebble.io/discord) — cross-post the forum thread.
      🟢 I'll draft the message.
- [ ] 🔵 **GitHub `pebble/community-resources` PR** — adds pebble-mcp to the
      official tools listing (permanent, low-effort). 🟢 I'll prep the branch +
      the markdown file; you submit the PR.
- [ ] Secondary / opportunistic: Show HN, r/pebble (check sidebar rules first),
      and a demo slot if the monthly dev hangout lands near launch.

---

### Status
Step 0 decisions are locked. Steps 1–4 (decouple, license, packaging, README, demo
GIF) are being prepared **now, in place** — so by the time you're done testing,
publishing is just: create the public repo (5b), configure the PyPI trusted
publisher (5), and push a `v0.1.0` tag (6). No rush — pull that trigger only once
the weekend's testing has you confident.
