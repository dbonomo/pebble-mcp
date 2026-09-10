"""Read-only MCP resources: reference data an agent can pull without a tool call.

Registers five ``pebble://`` resources, each returning a JSON-encoded string
with a stable top-level shape:

* ``pebble://platforms`` -- per-platform display specs (from :mod:`platforms`).
* ``pebble://colors`` -- the 64-color palette table + our house role guidance.
* ``pebble://fonts`` -- system font keys/sizes/usage, distilled from DESIGN.md.
* ``pebble://wire-conventions`` -- the reusable delimited wire-string pattern.
* ``pebble://touch-interaction`` -- swipe/tap handling on touch-capable
  Pebbles (emery/gabbro), and how touch coexists with the buttons.

All five are pure functions of in-repo data (platforms.py, palette.py,
DESIGN.md) -- no I/O, no toolchain, always available regardless of capability
tier.
"""

from __future__ import annotations

import json

from mcp.server.fastmcp import FastMCP

from pebble_mcp.palette import PALETTE
from pebble_mcp.platforms import platforms_resource

# ---------------------------------------------------------------------------
# pebble://colors
# ---------------------------------------------------------------------------
# Role guidance table, transcribed from DESIGN.md ("Our current palette (keep
# it small and consistent)"). Roles are named generically (bg/text/accent/
# good/attention/reserved) per the requirements doc's resource shape, with
# the two DESIGN.md text rows (primary/secondary) kept distinct since they
# carry different contrast obligations.
COLOR_ROLES: list[dict[str, str]] = [
    {
        "role": "background",
        "gcolor": "GColorBlack",
        "hex": "000000",
        "usage": "everywhere -- black background is the e-paper sweet spot",
    },
    {
        "role": "text_primary",
        "gcolor": "GColorWhite",
        "hex": "FFFFFF",
        "usage": "values, titles",
    },
    {
        "role": "text_secondary",
        "gcolor": "GColorLightGray",
        "hex": "AAAAAA",
        "usage": "hints, labels, stale markers",
    },
    {
        "role": "accent",
        "gcolor": "GColorVividCerulean",
        "hex": "00AAFF",
        "usage": "brand/identity -- headers, session chip, set counter",
    },
    {
        "role": "good",
        "gcolor": "GColorGreen",
        "hex": "00FF00",
        "usage": "on-pace / go -- protein on-pace, running timer, Send button",
    },
    {
        "role": "attention",
        "gcolor": "GColorChromeYellow",
        "hex": "FFAA00",
        "usage": "behind / needs-focus -- protein behind, reps focus, bonus time",
    },
    {
        "role": "success_flash",
        "gcolor": "GColorMalachite",
        "hex": "00FF55",
        "usage": (
            "logger confirmations. Note: the SDK name for 00FF55 is Malachite "
            "(not MediumSpringGreen, which is 00FFAA) -- trust palette.py over "
            "older docs that mislabel this."
        ),
    },
    {
        "role": "chrome",
        "gcolor": "GColorDarkGray",
        "hex": "555555",
        "usage": "separators, bar troughs -- mid-tones are for chrome only, never text fills",
    },
    {
        "role": "reserved",
        "gcolor": "GColorRed",
        "hex": "FF0000",
        "usage": (
            "genuinely-wrong states only (failed send, protein floor missed at "
            "day's end) -- currently unused elsewhere so it keeps its alarm value"
        ),
    },
]


def colors_resource() -> dict[str, object]:
    """Return the pebble://colors payload: the 64-color table + role guidance."""
    return {
        "colors": [
            {
                "name": c.name,
                "hex": c.hex,
                "corrected_hex": c.corrected_hex,
                "argb8": c.argb8,
            }
            for c in PALETTE
        ],
        "roles": COLOR_ROLES,
    }


# ---------------------------------------------------------------------------
# pebble://fonts — sourced from pebble_mcp.fonts, the authoritative SDK table
# ---------------------------------------------------------------------------


def fonts_resource() -> dict[str, object]:
    """Return the pebble://fonts payload: the full system font table + guidance."""
    from pebble_mcp.fonts import SYSTEM_FONTS

    return {
        "fonts": [
            {
                "key": f.key.removeprefix("FONT_KEY_"),
                "c_key": f.key,
                "family": f.family,
                "points": f.size,
                "weight": f.weight,
                "numbers_only": f.numbers_only,
                "emery_only": f.min_platform == "emery",
                "note": f.note,
            }
            for f in SYSTEM_FONTS
        ],
        "guidance": [
            "One hero value per screen, set in a LECO *_NUMBERS font.",
            "Numbers-only fonts (LECO/Bitham *_NUMBERS, Roboto subset) cannot "
            "render arbitrary text -- never route labels through them; use the "
            "font_plan tool to check glyph fit.",
            "Gothic scale runs GOTHIC_28_BOLD down to GOTHIC_14; GOTHIC_14 is the "
            "floor -- nothing smaller in our apps.",
            "Hints/labels live in the bottom ~40px of a screen, in the secondary "
            "text color (see pebble://colors role text_secondary).",
        ],
    }


# ---------------------------------------------------------------------------
# pebble://wire-conventions
# ---------------------------------------------------------------------------
def wire_conventions_resource() -> dict[str, object]:
    """Return the pebble://wire-conventions payload: the reusable delimited
    wire-string pattern used to move structured data from a phone companion
    (pkjs) to a Pebble C watchapp over a single AppMessage string.

    Generic write-up (no app-specific details) -- the illustrative example
    below is a stand-in shape, not a real project's wire format.
    """
    return {
        "overview": (
            "Pebble AppMessage keys carry typed values, but structured/variable "
            "-length data (a list of rows, several sections) is far simpler to "
            "move as ONE delimited string under ONE messageKey than as many "
            "typed keys. The companion (pkjs) fetches/reduces data into this "
            "string; the watch C code tokenizes it with strtok-style parsing. "
            "One engine, no per-field key bookkeeping, and the shape is easy to "
            "extend by appending a field or section."
        ),
        "delimiters": {
            "section": "~",
            "row": "|",
            "field": "^",
            "rationale": (
                "Three delimiter levels nest cleanly: sections separate logical "
                "groups (e.g. a header vs a list), rows separate repeated "
                "records within a section, fields separate a record's values. "
                "Pick characters that will never appear in real data (or "
                "sanitize them out -- see below)."
            ),
        },
        "example": {
            "description": (
                "Illustrative only -- a generic 'status + items' wire, not a real project's format."
            ),
            "layout": "status ~ items",
            "status": "level ^ label ^ age_min      level = G|Y|O|R",
            "items": "name ^ value ^ unit   | ... (repeated rows)",
            "sample": "G^All clear^4~Widget A^12^ct|Widget B^7^ct",
        },
        "sanitization": (
            "Any free text field (labels, names, notes) is sanitized before it "
            "goes into the wire string: strip/replace the delimiter characters "
            "themselves (and newlines) so a value can never accidentally start "
            "a new row, section, or field. Do this once, at the point the "
            "string is assembled in pkjs -- the watch-side parser should never "
            "have to defend against a malformed record."
        ),
        "integer_encoding": {
            "principle": (
                "Send integers, never floats -- the watch-side C parser "
                "(atoi/strtol) is simple and exact; float formatting/parsing "
                "across the JS<->C boundary is a source of drift and wasted "
                "code. When the true value has a fractional part, scale it to "
                "an integer and document the scale factor in the wire's header "
                "comment."
            ),
            "examples": [
                "A half-pound-resolution weight: send weight*2 as an int "
                "(e.g. 141 lb -> 282), watch divides by 2 to display.",
                "A tenths-of-an-inch snow total: send inches*10 as an int "
                "(e.g. 4.2in -> 42), watch divides by 10 to display.",
            ],
        },
        "staleness": {
            "pattern": "age_min",
            "description": (
                "Rather than sending an absolute timestamp (which requires "
                "watch and phone clocks to agree, and drifts if they don't), "
                "send age_min -- minutes elapsed between the source data's "
                "last-updated time and the moment pkjs sends the message. The "
                "watch then adds its own elapsed time since receipt to keep the "
                "displayed age current. This makes staleness display immune to "
                "clock skew between devices."
            ),
        },
        "message_key": (
            "Use exactly one AppMessage key (e.g. a single STATE_WIRE-style "
            "string key) for the whole payload rather than one key per field. "
            "This keeps the C-side dictionary small, avoids key-registration "
            "sprawl, and means adding a field is a wire-format change, not an "
            "AppMessage schema change."
        ),
        "bounded_copies": (
            "On the watch, copy the incoming string into a fixed-size stack or "
            "static buffer sized generously for the worst-case wire (not "
            "malloc'd per message), and treat overlength input defensively "
            "(truncate, don't overrun). Cap the number of rows a section can "
            "produce (both companion- and watch-side) so a pathological feed "
            "can't blow past the ~1-2KB practical AppMessage inbox budget or a "
            "fixed-size row array on the watch."
        ),
    }


# ---------------------------------------------------------------------------
# pebble://touch-interaction
# ---------------------------------------------------------------------------
# Distilled from reading a shipping touch-enabled store app (see the
# ``credit`` key below) plus the SDK's touch service API. Everything here is
# written in our own words as house guidance -- pebble-mcp is MIT and does not
# vendor, quote, or paraphrase-at-the-line-level any GPL-3.0 source.
def touch_interaction_resource() -> dict[str, object]:
    """Return the pebble://touch-interaction payload: how to handle swipes and
    taps on touch-capable Pebbles, and how touch coexists with the buttons.

    Touch hardware exists only on the newer platforms (``emery``,
    ``gabbro``); every other target is buttons-only, so touch code is always
    additive and always compiled out elsewhere.
    """
    return {
        "overview": (
            "The SDK's touch service is raw, not gestural: you subscribe once "
            "and receive three event kinds -- touchdown, position-update, and "
            "liftoff -- each carrying an x/y in screen pixels. There is no "
            "built-in swipe/fling/pinch recognizer and no multi-touch. A "
            "usable swipe is roughly thirty lines you write yourself, and the "
            "recommended shape is the liftoff-resolved delta below. Touch is "
            "an *addition* to the buttons, never a replacement: the same app "
            "binary runs on button-only hardware, and even on touch hardware "
            "the buttons stay live."
        ),
        "event_model": {
            "touchdown": (
                "Finger lands. Record the x/y into two module-static int16 "
                "fields and set an 'active' flag. Do no work here beyond "
                "that -- no move is committed on touchdown."
            ),
            "position_update": (
                "Fires repeatedly while the finger moves. IGNORE IT unless "
                "you are drawing live drag feedback (a tile that follows the "
                "finger, a scroll offset that tracks it). Handling it when "
                "you do not need it just burns CPU and redraws; a "
                "discrete-action UI should let these fall through to a "
                "default no-op branch."
            ),
            "liftoff": (
                "Finger leaves. This is where the gesture is resolved: "
                "compute dx/dy against the stored touchdown position, decide "
                "tap-vs-swipe and direction, clear the active flag, and "
                "dispatch the action."
            ),
        },
        "swipe_recognition": {
            "technique": (
                "Liftoff-resolved delta. dx = liftoff.x - touchdown.x, "
                "dy = liftoff.y - touchdown.y; take absolute values adx/ady."
            ),
            "jitter_threshold_px": 18,
            "threshold_note": (
                "If adx and ady are BOTH under the threshold, treat the "
                "gesture as a tap (or as noise) and commit no swipe. ~18px is "
                "a hand-tuned value that works well on emery-class screens -- "
                "it is not derived from a platform constant, so tune it per "
                "app rather than treating it as an SDK number."
            ),
            "direction": (
                "Dominant-axis wins: if adx > ady the swipe is horizontal "
                "(dx > 0 -> right, else left), otherwise vertical (dy > 0 -> "
                "down, else up, since Pebble's y grows downward). A diagonal "
                "therefore always collapses to one of four directions. No "
                "trigonometry, no 8-way handling -- for grid/list UIs the "
                "magnitude comparison is enough."
            ),
            "no_velocity_no_timers": (
                "No debounce timer, no minimum speed, no touchdown->liftoff "
                "timeout is needed for discrete actions: a slow deliberate "
                "drag past the threshold and a fast flick mean the same "
                "thing. Only reach for velocity if the app genuinely needs "
                "fling/inertia."
            ),
            "state_cost": (
                "The entire recognizer is two int16s plus one bool of "
                "module-static state. Keep it that small."
            ),
            "stale_liftoff_guard": (
                "Bail out of the liftoff handler when the active flag is "
                "false. A liftoff can arrive with no matching touchdown -- a "
                "touch that began before your window was frontmost, or a pair "
                "split across a subscribe/unsubscribe boundary -- and without "
                "the guard you compute a delta against stale coordinates and "
                "fire a phantom swipe."
            ),
        },
        "buttons_and_touch_coexist": {
            "principle": (
                "Both input paths are subscribed at the same time on touch "
                "hardware, and both call the SAME action functions (one "
                "apply_move()/do_action() per logical action). Never let a "
                "gesture and a button reach the model through different code "
                "paths -- that is how the two inputs drift into states the "
                "other does not understand."
            ),
            "mapping_example": (
                "A 4-direction game: UP/DOWN buttons and vertical swipes hit "
                "the same two actions; SELECT short-press and a right swipe "
                "hit a third; BACK short-press and a left swipe hit a fourth. "
                "Long-press SELECT opens a confirm overlay."
            ),
            "modal_gating": (
                "Gate touch with the same modal/overlay state machine that "
                "gates buttons, and write that gating out explicitly in BOTH "
                "handlers rather than sharing one clever helper -- the two "
                "paths want subtly different behavior (below), and the "
                "duplication reads clearer than the abstraction."
            ),
            "dismiss_vs_commit": (
                "Useful asymmetry for confirm dialogs and destructive "
                "actions: ANY touch dismisses/cancels, but only a real "
                "button press (SELECT) commits. Touch is cheap and easy to "
                "trigger by accident; a physical click is deliberate."
            ),
            "swallow_the_dismissing_gesture": (
                "When an overlay (an idle 'still there?' prompt, a toast) is "
                "up, let touchdown dismiss it but do NOT set the active flag. "
                "Because liftoff no-ops without that flag, the same gesture "
                "cannot both dismiss the overlay and fire an action "
                "underneath it. The button equivalent is 'handle the dismiss, "
                "then return early'."
            ),
            "implicit_targets": (
                "On a terminal screen (game over, error), it is reasonable "
                "to make the whole surface the restart target: any touch or "
                "any button starts over, with no drawn affordance."
            ),
        },
        "platform_gating": {
            "macro": "PBL_TOUCH",
            "guidance": (
                "Wrap the handler, the subscribe/unsubscribe helpers, and the "
                "static state in #ifdef PBL_TOUCH (declaration in the header "
                "too), so button-only builds (aplite/basalt/chalk/diorite) "
                "carry zero touch code and zero touch-service overhead. "
                "Buttons must remain fully sufficient on those platforms -- "
                "no feature may be touch-only."
            ),
            "touch_platforms": ["emery", "gabbro"],
        },
        "lifecycle": {
            "pairing": (
                "Subscribe right after the window is pushed; unsubscribe in "
                "deinit, symmetric with window teardown. Unpaired subscribes "
                "leak events into a torn-down UI."
            ),
            "runtime_gate": (
                "Guard both the subscribe and the unsubscribe on "
                "touch_service_is_enabled(). Touch-capable hardware does not "
                "guarantee the service is on -- firmware version or a user "
                "setting can disable it -- and the sensor draws power while "
                "enabled, so check the runtime capability instead of "
                "inferring it from the platform macro."
            ),
        },
        "gotchas": [
            "BACK long-press exit is handled by the firmware, not the app: "
            "the OS force-exits while the button is still held, so the app "
            "never sees the release. Wiring BACK with a plain single-click "
            "subscription races that exit. Use a multi-click subscription "
            "(min 1, max 1, ~50ms timeout, last_click_only) so the handler "
            "fires shortly after release rather than on press.",
            "Position-update events fire a lot. Subscribing to logic you do "
            "not need there is the easiest way to make a touch app feel "
            "sluggish.",
            "There is no tap-target hit-testing helper: if you draw an "
            "on-screen button, you compare the event x/y against the layer's "
            "bounds yourself. A whole-screen swipe surface needs none of "
            "that -- prefer it when the UI allows.",
            "Single-point only. Do not design around pinch, two-finger, or simultaneous touches.",
        ],
        "credit": (
            "Patterns observed in '2048 Touch' by vorsk/lanrat (store id "
            "6df87b64b7174448a065ef54), source at "
            "https://github.com/lanrat/pebble-2048-touch (GPL-3.0). This "
            "resource is an independent description of the techniques, "
            "written for pebble-mcp (MIT); no upstream code is reproduced "
            "here. If you copy implementation from that repository into a "
            "project, that project takes on GPL-3.0 obligations."
        ),
    }


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------
def register(mcp: FastMCP) -> None:
    """Register all pebble:// read-only resources on the given FastMCP server."""

    @mcp.resource("pebble://platforms")
    def platforms() -> str:
        """Per-platform Pebble display specs (emery, basalt, chalk, diorite)."""
        return json.dumps(platforms_resource(), indent=2)

    @mcp.resource("pebble://colors")
    def colors() -> str:
        """The 64-color Pebble palette table plus our house role guidance."""
        return json.dumps(colors_resource(), indent=2)

    @mcp.resource("pebble://fonts")
    def fonts() -> str:
        """System font keys/sizes/usage guidance distilled from DESIGN.md."""
        return json.dumps(fonts_resource(), indent=2)

    @mcp.resource("pebble://wire-conventions")
    def wire_conventions() -> str:
        """The reusable delimited wire-string pattern for a Pebble companion protocol."""
        return json.dumps(wire_conventions_resource(), indent=2)

    @mcp.resource("pebble://touch-interaction")
    def touch_interaction() -> str:
        """Swipe/tap handling on touch Pebbles (emery, gabbro) and how touch
        coexists with the physical buttons."""
        return json.dumps(touch_interaction_resource(), indent=2)
