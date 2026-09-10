"""Review / test tier — the "is my watchface any good, and does it run?" layer.

Two pure-ish capabilities, both built by *reusing* the existing libraries rather
than duplicating any palette / contrast / image / build logic:

* :func:`analyze` — a structured, actionable design critique of a single
  screenshot, judged against the Pebble design system. Pure Pillow + palette
  math; no toolchain or network. Reuses :mod:`pebble_mcp.palette`
  (nearest-color + WCAG contrast), :mod:`pebble_mcp.images` (loading), and the
  ``COLOR_ROLES`` house guidance from :mod:`pebble_mcp.resources`.
* :func:`smoke_test` — the one-call "does my app build, install, and what does
  its launch screen look like" check. A thin orchestration over
  :func:`pebble_mcp.devloop.build` / :func:`~pebble_mcp.devloop.install` plus a
  single ``pebble screenshot``; Tier-3 (gated on the ``pebble`` CLI by the
  devloop functions it calls).

## The design-review heuristic (and its limits)

`analyze` is a *heuristic*, not a human designer. It reasons about pixels and
colour statistics, never about semantics — it cannot see "this is a label" or
"this numeral is the hero value". Concretely:

* **Palette adherence** is exact and trustworthy: every pixel is either one of
  the 64 ``GColor`` values or it is not. Off-palette colours are grouped and the
  most-common ones are reported with their nearest ``GColor`` (via
  :func:`palette.nearest`). Real hardware only ever shows the 64, so anything
  off-palette is a quantization gap the emulator/browser is hiding.

* **Contrast/legibility** samples an ``N×N`` grid of regions. Within each region
  the two most-common colours are taken as a ``(background, ink)`` pair — the
  assumption being that a Pebble UI region is a mostly-flat background with text
  or a glyph drawn on top. Their WCAG contrast is computed against the
  **sunlight-corrected** display values (``PaletteColor.corrected_hex``), because
  the reflective always-on LCD reads noticeably duller than bright sRGB
  (DESIGN.md: "the emulator lies bright"). A region whose dominant pair falls
  below the legibility threshold — and whose ink covers a non-trivial slice of
  the region — is flagged.
  *Limits:* it counts colour dominance, not glyphs. A region that is genuinely a
  solid block, a decorative low-contrast border, or heavy anti-aliasing can
  false-positive or false-negative. It never proves text *is* legible; it flags
  pairs that *statistically look* illegible so a human (or a follow-up
  screenshot) can check.

* **Role guidance** maps the significantly-present on-palette colours to the
  ``COLOR_ROLES`` table and emits advisory notes for the two house rules a
  screenshot can betray: ``GColorRed`` used for non-alarm content, and mid-grey
  (``GColorDarkGray``) used for content when the guidance reserves mid-tones for
  chrome. It cannot tell text from chrome, so these are worded as advisories.

Output is deliberately compact (a summary + bounded lists), never a per-pixel
dump — context is the scarce resource (see TOOL-SURFACE.md §3).
"""

from __future__ import annotations

import os
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from . import images, palette
from .flow import PebbleRunner, _is_png, looks_wedged, subprocess_runner
from .palette import PALETTE
from .resources import COLOR_ROLES

# --------------------------------------------------------------------------- #
# Tunables (all bounded so a return is always compact)
# --------------------------------------------------------------------------- #
# Cap the working image size for the colour histogram. Pebble screens are tiny
# (emery is 200×228 ≈ 45.6k px), so real screenshots are never touched; only an
# oversized source is down-sampled — with NEAREST, which picks existing pixels
# and so keeps an on-palette image exactly on-palette.
WORKING_PIXEL_BUDGET = 65_536  # 256×256

# Default region grid for the contrast sweep (N×N).
DEFAULT_REGIONS = 3

# Bounds on every list the review returns.
MAX_OFF_PALETTE = 8
MAX_LOW_CONTRAST = 6
MAX_ROLE_NOTES = 6
MAX_SUGGESTIONS = 8
MAX_DOMINANT_ROLES = 6

# A region's second colour must cover at least this fraction of the region to
# count as "ink" worth a contrast check (filters anti-alias speckle / near-solid
# blocks from producing noise flags).
MIN_INK_FRACTION = 0.03

# A colour must cover at least this fraction of the whole image before a role
# advisory fires for it — keeps a stray pixel from raising a warning.
MIN_ROLE_FRACTION = 0.01

# The legibility floor. Reuses palette's tuned reflective-LCD threshold.
LEGIBLE_THRESHOLD = palette.LEGIBLE_THRESHOLD

# Precomputed lookups (built once, reused) — no duplication of palette data.
_PALETTE_RGBS: set[tuple[int, int, int]] = {c.rgb for c in PALETTE}
_NAME_BY_RGB: dict[tuple[int, int, int], str] = {c.rgb: c.name for c in PALETTE}
# uncorrected palette rgb -> sunlight-corrected display rgb (how the LCD reads).
_CORRECTED_RGB: dict[tuple[int, int, int], tuple[int, int, int]] = {
    c.rgb: palette._hex_to_rgb(c.corrected_hex) for c in PALETTE
}
_ROLE_BY_HEX: dict[str, dict[str, str]] = {r["hex"]: r for r in COLOR_ROLES}


# --------------------------------------------------------------------------- #
# Result shape
# --------------------------------------------------------------------------- #
@dataclass
class DesignReview:
    """Structured critique of one screenshot. See :func:`analyze`.

    Every list field is bounded (see the ``MAX_*`` module constants) so the
    serialized form stays compact regardless of the input image.
    """

    width: int
    height: int
    palette_clean: bool
    on_palette_fraction: float
    distinct_colors: int
    off_palette: list[dict[str, Any]]
    off_palette_color_count: int
    dominant_bg: dict[str, Any]
    contrast_pairs: list[dict[str, Any]]
    low_contrast_regions: int
    dominant_roles: list[dict[str, Any]]
    role_notes: list[str]
    suggestions: list[str]
    summary: str

    def to_dict(self) -> dict[str, Any]:
        """The compact, agent-facing critique dict (stable top-level shape)."""
        return {
            "summary": self.summary,
            "size": {"width": self.width, "height": self.height},
            "palette": {
                "clean": self.palette_clean,
                "on_palette_fraction": self.on_palette_fraction,
                "distinct_colors": self.distinct_colors,
                "off_palette_color_count": self.off_palette_color_count,
                "off_palette": self.off_palette,
            },
            "contrast": {
                "threshold": LEGIBLE_THRESHOLD,
                "judged_against": "sunlight-corrected display values",
                "dominant_bg": self.dominant_bg,
                "low_contrast_regions": self.low_contrast_regions,
                "pairs": self.contrast_pairs,
            },
            "roles": {
                "dominant_roles": self.dominant_roles,
                "notes": self.role_notes,
            },
            "suggestions": self.suggestions,
        }


# --------------------------------------------------------------------------- #
# Working image + histogram helpers
# --------------------------------------------------------------------------- #
def _working_rgb(img: images.Image.Image) -> images.Image.Image:
    """RGB copy of ``img``, down-sampled (NEAREST) if it exceeds the budget.

    NEAREST resampling only ever picks pixels that already exist, so a clean
    palette image stays exactly on-palette after shrinking — the adherence
    fraction is not polluted by interpolation.
    """
    from PIL import Image as _Image

    rgb = img.convert("RGB")
    w, h = rgb.size
    if w * h <= WORKING_PIXEL_BUDGET or w == 0 or h == 0:
        return rgb
    scale = (WORKING_PIXEL_BUDGET / (w * h)) ** 0.5
    nw, nh = max(1, int(w * scale)), max(1, int(h * scale))
    return rgb.resize((nw, nh), _Image.Resampling.NEAREST)


def _histogram(rgb: images.Image.Image) -> list[tuple[int, tuple[int, int, int]]]:
    """``[(count, (r,g,b)), ...]`` for the whole image, most-common first.

    Bounded because the caller always passes a working image capped at
    :data:`WORKING_PIXEL_BUDGET` pixels, so ``getcolors`` never returns None.
    """
    w, h = rgb.size
    colors = rgb.getcolors(maxcolors=max(1, w * h)) or []
    colors.sort(key=lambda cr: (-cr[0], cr[1]))
    return colors


def _display(rgb: tuple[int, int, int]) -> tuple[int, int, int]:
    """Map a colour to how the reflective LCD renders it (corrected for palette
    entries; unchanged for off-palette colours, which never reach real hardware)."""
    return _CORRECTED_RGB.get(rgb, rgb)


def _corrected_contrast(a: tuple[int, int, int], b: tuple[int, int, int]) -> float:
    """WCAG contrast of two colours as the display shows them (reuses palette)."""
    return palette.contrast_ratio(_display(a), _display(b))


def _color_label(rgb: tuple[int, int, int]) -> str | None:
    """The ``GColor`` name for an on-palette colour, else ``None``."""
    return _NAME_BY_RGB.get(rgb)


# --------------------------------------------------------------------------- #
# The three analyses
# --------------------------------------------------------------------------- #
def _palette_adherence(
    hist: list[tuple[int, tuple[int, int, int]]], total: int
) -> tuple[float, list[dict[str, Any]], int]:
    """Fraction on-palette, plus the most-common off-palette colours + nearest."""
    on = 0
    off: list[tuple[int, tuple[int, int, int]]] = []
    for count, rgb in hist:
        if rgb in _PALETTE_RGBS:
            on += count
        else:
            off.append((count, rgb))
    fraction = round(on / total, 4) if total else 1.0

    reported: list[dict[str, Any]] = []
    for count, rgb in off[:MAX_OFF_PALETTE]:
        match = palette.nearest(rgb)
        reported.append(
            {
                "hex": palette.rgb_to_hex(rgb),
                "count": count,
                "fraction": round(count / total, 4) if total else 0.0,
                "nearest_name": match.name,
                "nearest_hex": match.hex,
                "distance": match.distance,
            }
        )
    return fraction, reported, len(off)


def _contrast_sweep(
    rgb_img: images.Image.Image, regions: int
) -> tuple[dict[str, Any], list[dict[str, Any]], int]:
    """Grid contrast sweep. Returns (dominant_bg, flagged_pairs, low_count).

    Each region contributes its dominant ``(bg, ink)`` pair; only pairs below the
    legibility threshold (with meaningful ink coverage) are reported, most severe
    (lowest ratio) first.
    """
    w, h = rgb_img.size
    flagged: list[dict[str, Any]] = []
    for gy in range(regions):
        for gx in range(regions):
            x0, x1 = w * gx // regions, w * (gx + 1) // regions
            y0, y1 = h * gy // regions, h * (gy + 1) // regions
            if x1 <= x0 or y1 <= y0:
                continue
            cell = rgb_img.crop((x0, y0, x1, y1))
            chist = _histogram(cell)
            if len(chist) < 2:
                continue  # solid region: nothing to read against
            cell_total = sum(c for c, _ in chist)
            bg_count, bg = chist[0]
            ink_count, ink = chist[1]
            if cell_total == 0 or ink_count / cell_total < MIN_INK_FRACTION:
                continue
            ratio = _corrected_contrast(ink, bg)
            if ratio >= LEGIBLE_THRESHOLD:
                continue
            flagged.append(
                {
                    "region": f"r{gy}c{gx}",
                    "ink_hex": palette.rgb_to_hex(ink),
                    "ink_name": _color_label(ink),
                    "bg_hex": palette.rgb_to_hex(bg),
                    "bg_name": _color_label(bg),
                    "ratio": ratio,
                    "legible": False,
                }
            )
    flagged.sort(key=lambda p: p["ratio"])
    low_count = len(flagged)

    dominant_bg: dict[str, Any] = {}
    whole = _histogram(rgb_img)
    if whole:
        _, dbg = whole[0]
        dominant_bg = {"hex": palette.rgb_to_hex(dbg), "name": _color_label(dbg)}

    return dominant_bg, flagged[:MAX_LOW_CONTRAST], low_count


def _role_guidance(
    hist: list[tuple[int, tuple[int, int, int]]], total: int
) -> tuple[list[dict[str, Any]], list[str]]:
    """Map dominant colours to roles and emit house-rule advisories."""
    dominant_roles: list[dict[str, Any]] = []
    notes: list[str] = []
    if not total:
        return dominant_roles, notes

    # Fraction per palette colour present (off-palette colours have no role).
    frac_by_hex: dict[str, float] = {}
    for count, rgb in hist:
        name = _NAME_BY_RGB.get(rgb)
        if name is None:
            continue
        hx = palette.rgb_to_hex(rgb)
        frac_by_hex[hx] = frac_by_hex.get(hx, 0.0) + count / total

    for hx, frac in sorted(frac_by_hex.items(), key=lambda kv: -kv[1]):
        role = _ROLE_BY_HEX.get(hx)
        if role is None or frac < MIN_ROLE_FRACTION:
            continue
        if len(dominant_roles) < MAX_DOMINANT_ROLES:
            dominant_roles.append(
                {
                    "hex": hx,
                    "name": role["gcolor"],
                    "role": role["role"],
                    "fraction": round(frac, 4),
                }
            )

    # House-rule advisories (heuristic — pixels can't reveal semantics).
    red_frac = frac_by_hex.get("FF0000", 0.0)
    if red_frac >= MIN_ROLE_FRACTION:
        notes.append(
            f"GColorRed covers {round(red_frac * 100, 1)}% of the screen. Red is "
            "reserved for genuinely-wrong/alarm states only (role 'reserved'); "
            "using it for ordinary content bleeds its alarm value."
        )
    gray_frac = frac_by_hex.get("555555", 0.0)
    # Only advise when mid-grey is present but not the dominant background.
    if MIN_ROLE_FRACTION <= gray_frac < 0.5:
        notes.append(
            "GColorDarkGray (555555) is present. Mid-tones are chrome-only per "
            "role guidance (separators/bar troughs) — if this is a text fill, "
            "switch to GColorLightGray (AAAAAA) for secondary text."
        )
    return dominant_roles, notes[:MAX_ROLE_NOTES]


# --------------------------------------------------------------------------- #
# Public: analyze
# --------------------------------------------------------------------------- #
def analyze(img: images.Image.Image, *, regions: int = DEFAULT_REGIONS) -> DesignReview:
    """Critique ``img`` against the Pebble design system.

    Args:
        img: a decoded PIL image (any mode; converted to RGB internally).
        regions: the ``N×N`` grid resolution for the contrast sweep (default 3,
            clamped to ``[1, 8]``).

    Returns:
        A :class:`DesignReview`; call :meth:`DesignReview.to_dict` for the
        compact agent-facing critique.
    """
    regions = max(1, min(8, int(regions)))
    rgb = _working_rgb(img)
    w, h = rgb.size
    hist = _histogram(rgb)
    total = sum(c for c, _ in hist)

    on_frac, off_palette, off_count = _palette_adherence(hist, total)
    dominant_bg, pairs, low_count = _contrast_sweep(rgb, regions)
    dominant_roles, role_notes = _role_guidance(hist, total)

    suggestions = _suggestions(on_frac, off_palette, pairs, low_count, role_notes)
    summary = _summary(on_frac, off_count, low_count, role_notes)

    return DesignReview(
        width=w,
        height=h,
        palette_clean=off_count == 0,
        on_palette_fraction=on_frac,
        distinct_colors=len(hist),
        off_palette=off_palette,
        off_palette_color_count=off_count,
        dominant_bg=dominant_bg,
        contrast_pairs=pairs,
        low_contrast_regions=low_count,
        dominant_roles=dominant_roles,
        role_notes=role_notes,
        suggestions=suggestions,
        summary=summary,
    )


def _suggestions(
    on_frac: float,
    off_palette: list[dict[str, Any]],
    pairs: list[dict[str, Any]],
    low_count: int,
    role_notes: list[str],
) -> list[str]:
    out: list[str] = []
    if off_palette:
        worst = off_palette[0]
        out.append(
            f"Image is only {round(on_frac * 100, 1)}% palette-clean. Real "
            "hardware shows just the 64 GColors — run image_quantize to snap it "
            f"on-palette (largest offender #{worst['hex']} -> {worst['nearest_name']})."
        )
    for p in pairs[: min(3, MAX_LOW_CONTRAST)]:
        ink = p["ink_name"] or f"#{p['ink_hex']}"
        bg = p["bg_name"] or f"#{p['bg_hex']}"
        out.append(
            f"Region {p['region']}: {ink} on {bg} is only {p['ratio']}:1 "
            f"(below {LEGIBLE_THRESHOLD}:1). Increase contrast — white/black ink, "
            "or reserve mid-tones for chrome."
        )
    out.extend(role_notes)
    if not out:
        out.append(
            "Palette-clean and legible against the corrected display — no issues "
            "found by the heuristic. Confirm hero-value hierarchy by eye."
        )
    return out[:MAX_SUGGESTIONS]


def _summary(on_frac: float, off_count: int, low_count: int, role_notes: list[str]) -> str:
    palette_part = (
        "palette-clean" if off_count == 0 else f"{round(on_frac * 100, 1)}% palette-clean"
    )
    contrast_part = (
        "no low-contrast regions"
        if low_count == 0
        else f"{low_count} low-contrast region{'s' if low_count != 1 else ''}"
    )
    role_part = (
        "no role warnings"
        if not role_notes
        else f"{len(role_notes)} role warning{'s' if len(role_notes) != 1 else ''}"
    )
    return f"{palette_part}; {contrast_part}; {role_part}."


# --------------------------------------------------------------------------- #
# Public: smoke_test (Tier 3)
# --------------------------------------------------------------------------- #
@dataclass
class SmokeResult:
    """Outcome of :func:`smoke_test` — build → install → launch screenshot."""

    built: bool
    installed: bool
    platform: str
    errors: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[dict[str, Any]] = field(default_factory=list)
    pbw_path: str | None = None
    launch_shot: str | None = None
    returncode: int = 0
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "built": self.built,
            "installed": self.installed,
            "platform": self.platform,
            "returncode": self.returncode,
            "errors": self.errors,
            "warnings": self.warnings,
            "pbw_path": self.pbw_path,
            "launch_shot": self.launch_shot,
            "notes": self.notes,
        }


def _diag_dict(d: Any) -> dict[str, Any]:
    """Compact projection of a devloop.Diagnostic (no raw dump)."""
    return {"severity": d.severity, "message": d.message, "file": d.file, "line": d.line}


def _capture_launch_shot(platform: str, runner: PebbleRunner, dest: str) -> tuple[str | None, str]:
    """One ``pebble screenshot`` of the running emulator. Returns (path|None, note)."""
    rc, out = runner(["pebble", "screenshot", "--no-open", dest], None, 90)
    if rc == 0 and os.path.exists(dest) and os.path.getsize(dest) > 0 and _is_png(dest):
        return dest, "captured launch screenshot"
    hint = (
        "emulator looks wedged (try emu_stop wipe=True then re-run)"
        if looks_wedged(out)
        else "no valid PNG produced"
    )
    return None, f"launch screenshot failed (rc={rc}): {hint}"


def smoke_test(
    project_dir: str,
    platform: str = "emery",
    *,
    runner: PebbleRunner = subprocess_runner,
    sleep: Callable[[float], None] = time.sleep,
    shot_dir: str | None = None,
) -> SmokeResult:
    """Build, install, and screenshot a project — the one-call "does it run?".

    Builds ``project_dir`` (via :func:`devloop.build`); on failure returns
    ``built=False`` with the compiler errors and *skips* install. On a good
    build it installs the freshly-built ``.pbw`` (kill+wipe-first, via
    :func:`devloop.install`) and captures a single launch screenshot.

    Tier-3: the underlying build/install raise
    :class:`~pebble_mcp.devloop.PebbleUnavailableError` when the ``pebble`` CLI
    is absent — callers surface that as the gate.

    ``runner``/``sleep`` are injectable (the shared
    :data:`~pebble_mcp.flow.PebbleRunner` seam) so tests never touch a real
    emulator.
    """
    # Imported here so a module import never hard-requires the Tier-3 stack.
    from . import devloop

    build_res = devloop.build(project_dir, runner=runner)
    if not build_res.success or not build_res.pbw_path:
        return SmokeResult(
            built=False,
            installed=False,
            platform=platform,
            errors=[_diag_dict(d) for d in build_res.errors],
            warnings=[_diag_dict(d) for d in build_res.warnings],
            returncode=build_res.returncode,
            notes=["build failed — install and screenshot skipped"],
        )

    warnings = [_diag_dict(d) for d in build_res.warnings]
    install_res = devloop.install(build_res.pbw_path, platform, runner=runner, sleep=sleep)
    if not install_res.success:
        return SmokeResult(
            built=True,
            installed=False,
            platform=platform,
            warnings=warnings,
            pbw_path=build_res.pbw_path,
            returncode=install_res.returncode,
            notes=["install failed — launch screenshot skipped"],
        )

    shot_dir = shot_dir or tempfile.mkdtemp(prefix="pebble-smoke-")
    dest = os.path.join(shot_dir, "launch.png")
    shot_path, note = _capture_launch_shot(platform, runner, dest)
    return SmokeResult(
        built=True,
        installed=True,
        platform=platform,
        warnings=warnings,
        pbw_path=build_res.pbw_path,
        launch_shot=shot_path,
        returncode=0,
        notes=[note],
    )
