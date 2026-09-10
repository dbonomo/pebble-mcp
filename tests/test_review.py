"""Tests for pebble_mcp.review — the design-critique + smoke-test library.

`analyze` is exercised on synthetic Pillow images with KNOWN properties so the
heuristic's claims are checkable; `smoke_test` is driven by a stub ``pebble``
runner (the shared PebbleRunner seam) and never touches a real emulator.
"""

from __future__ import annotations

import os

from PIL import Image

from pebble_mcp import review

BLACK = (0, 0, 0)
WHITE = (255, 255, 255)
GREEN = (0, 255, 0)
DARKGRAY = (85, 85, 85)
RED = (255, 0, 0)


def _band(bg, band_color, *, size=(60, 60), band=(0, 27, 60, 33)) -> Image.Image:
    """A solid ``bg`` image with a horizontal ``band_color`` stripe in the middle."""
    img = Image.new("RGB", size, bg)
    x0, y0, x1, y1 = band
    img.paste(Image.new("RGB", (x1 - x0, y1 - y0), band_color), (x0, y0))
    return img


def _checker(a, b, size=(40, 40)) -> Image.Image:
    img = Image.new("RGB", size, a)
    for y in range(size[1]):
        for x in range(size[0]):
            if (x + y) % 2:
                img.putpixel((x, y), b)
    return img


# --------------------------------------------------------------------------- #
# Palette adherence
# --------------------------------------------------------------------------- #
def test_on_palette_image_reports_full_adherence():
    r = review.analyze(_checker(BLACK, WHITE))
    assert r.palette_clean is True
    assert r.on_palette_fraction == 1.0
    assert r.off_palette_color_count == 0
    assert r.off_palette == []


def test_off_palette_gradient_reports_nearest_matches():
    grad = Image.new("RGB", (64, 64))
    for y in range(64):
        for x in range(64):
            # +1 so channel values are 1/4/7/... — never on the 0/85/170/255 grid.
            grad.putpixel((x, y), (x * 3 + 1, y * 3 + 1, (x + y) + 1))
    r = review.analyze(grad)
    assert r.palette_clean is False
    assert r.on_palette_fraction < 1.0
    assert r.off_palette_color_count > 0
    # Reported off-palette list is bounded and each has a sensible nearest GColor.
    assert 0 < len(r.off_palette) <= review.MAX_OFF_PALETTE
    for entry in r.off_palette:
        assert entry["nearest_hex"] != entry["hex"]  # it IS off-palette
        assert entry["distance"] >= 0
    # A near-black off-palette pixel snaps to GColorBlack.
    near_black = next((e for e in r.off_palette if e["hex"] == "010101"), None)
    if near_black is not None:
        assert near_black["nearest_name"] == "GColorBlack"


def test_partial_palette_image_fraction_between_zero_and_one():
    # Half on-palette (black), half a single off-palette colour.
    img = Image.new("RGB", (40, 40), BLACK)
    img.paste(Image.new("RGB", (40, 20), (1, 2, 3)), (0, 20))
    r = review.analyze(img)
    assert 0.0 < r.on_palette_fraction < 1.0
    assert r.off_palette_color_count == 1


# --------------------------------------------------------------------------- #
# Contrast / legibility
# --------------------------------------------------------------------------- #
def test_white_on_black_passes_contrast():
    r = review.analyze(_band(BLACK, WHITE))
    assert r.low_contrast_regions == 0
    assert r.contrast_pairs == []


def test_dark_gray_on_black_fails_contrast():
    r = review.analyze(_band(BLACK, DARKGRAY))
    assert r.low_contrast_regions >= 1
    # Every flagged pair is genuinely below the threshold and marked illegible.
    for p in r.contrast_pairs:
        assert p["ratio"] < review.LEGIBLE_THRESHOLD
        assert p["legible"] is False
    # And a concrete suggestion names the region.
    assert any("Region" in s for s in r.suggestions)


def test_contrast_judged_against_corrected_display_values():
    # DarkGray on black: the SWEEP must use corrected values. Uncorrected 555555
    # vs 000000 and corrected 545454 vs 000000 both fail, so we assert the pair
    # ratio matches the CORRECTED computation, not the raw one.
    from pebble_mcp import palette

    r = review.analyze(_band(BLACK, DARKGRAY))
    assert r.contrast_pairs
    corrected = palette.contrast_ratio((0x54, 0x54, 0x54), (0, 0, 0))
    assert r.contrast_pairs[0]["ratio"] == corrected


# --------------------------------------------------------------------------- #
# Role guidance
# --------------------------------------------------------------------------- #
def test_red_content_triggers_reserved_role_note():
    r = review.analyze(_band(BLACK, RED))
    assert any("GColorRed" in n and "reserved" in n for n in r.role_notes)


def test_mid_gray_triggers_chrome_only_note():
    r = review.analyze(_band(BLACK, DARKGRAY))
    assert any("GColorDarkGray" in n and "chrome" in n for n in r.role_notes)


def test_dominant_roles_map_known_colors():
    r = review.analyze(_checker(BLACK, WHITE))
    roles = {d["role"] for d in r.dominant_roles}
    assert "background" in roles  # black
    assert "text_primary" in roles  # white


# --------------------------------------------------------------------------- #
# Output shape — stable and compact
# --------------------------------------------------------------------------- #
def test_output_shape_is_stable_and_compact():
    r = review.analyze(_band(BLACK, DARKGRAY))
    d = r.to_dict()
    assert set(d) == {"summary", "size", "palette", "contrast", "roles", "suggestions"}
    assert set(d["palette"]) == {
        "clean",
        "on_palette_fraction",
        "distinct_colors",
        "off_palette_color_count",
        "off_palette",
    }
    assert set(d["contrast"]) == {
        "threshold",
        "judged_against",
        "dominant_bg",
        "low_contrast_regions",
        "pairs",
    }
    assert set(d["roles"]) == {"dominant_roles", "notes"}
    # All lists are bounded.
    assert len(d["palette"]["off_palette"]) <= review.MAX_OFF_PALETTE
    assert len(d["contrast"]["pairs"]) <= review.MAX_LOW_CONTRAST
    assert len(d["roles"]["notes"]) <= review.MAX_ROLE_NOTES
    assert len(d["roles"]["dominant_roles"]) <= review.MAX_DOMINANT_ROLES
    assert len(d["suggestions"]) <= review.MAX_SUGGESTIONS
    assert isinstance(d["summary"], str) and d["summary"]


def test_clean_legible_image_still_yields_a_suggestion():
    # A palette-clean, high-contrast image produces a "no issues" style note,
    # never an empty suggestions list.
    r = review.analyze(_checker(BLACK, WHITE))
    assert r.suggestions
    assert r.low_contrast_regions == 0


def test_large_image_downsampled_but_stays_on_palette():
    # An oversized on-palette image is NEAREST-downsampled for the histogram and
    # must remain reported as fully on-palette (no interpolation artifacts).
    big = _checker(BLACK, WHITE, size=(400, 400))
    assert big.size[0] * big.size[1] > review.WORKING_PIXEL_BUDGET
    r = review.analyze(big)
    assert r.palette_clean is True
    assert r.on_palette_fraction == 1.0


# --------------------------------------------------------------------------- #
# smoke_test — build / install / screenshot via a stub runner
# --------------------------------------------------------------------------- #
WAF_SUCCESS = (
    "[20/20] Creating app_bundle:  -> build/app.pbw\n'build' finished successfully (0.1s)\n"
)
WAF_ERROR = "../src/c/app.c:5:3: error: 'x' undeclared (first use in this function)\nBuild failed\n"


class SmokeRunner:
    """Stub ``pebble`` runner: canned build output, writes a PNG on screenshot."""

    def __init__(self, build_out: str = WAF_SUCCESS, build_rc: int = 0):
        self.build_out = build_out
        self.build_rc = build_rc
        self.calls: list[list[str]] = []

    def __call__(self, cmd, cwd=None, timeout=120):
        self.calls.append(cmd)
        joined = " ".join(cmd)
        if "pebble build" in joined:
            return self.build_rc, self.build_out
        if cmd[:2] == ["pebble", "screenshot"]:
            with open(cmd[-1], "wb") as f:
                f.write(b"\x89PNG\r\n\x1a\n")
            return 0, "ok"
        return 0, "ok"

    def cmds(self) -> list[str]:
        return [" ".join(c) for c in self.calls]


def _no_sleep(_s):
    pass


def _project(tmp_path):
    proj = tmp_path / "app"
    (proj / "build").mkdir(parents=True)
    (proj / "build" / "app.pbw").write_bytes(b"PK")
    return proj


def test_smoke_test_build_failure_skips_install(tmp_path, monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/pebble")
    proj = _project(tmp_path)
    runner = SmokeRunner(build_out=WAF_ERROR, build_rc=1)
    res = review.smoke_test(str(proj), runner=runner, sleep=_no_sleep)
    assert res.built is False
    assert res.installed is False
    assert res.launch_shot is None
    assert res.errors and res.errors[0]["message"].startswith("'x' undeclared")
    # Never touched the emulator.
    assert runner.cmds() == ["pebble build"]


def test_smoke_test_build_ok_installs_and_screenshots(tmp_path, monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/pebble")
    proj = _project(tmp_path)
    runner = SmokeRunner()
    res = review.smoke_test(str(proj), runner=runner, sleep=_no_sleep)
    assert res.built is True
    assert res.installed is True
    assert res.launch_shot is not None and os.path.exists(res.launch_shot)
    assert review._is_png(res.launch_shot)
    cmds = runner.cmds()
    assert cmds[0] == "pebble build"
    assert "pebble kill" in cmds and "pebble wipe" in cmds
    assert any(c.startswith("pebble install --emulator emery ") for c in cmds)
    assert any(c.startswith("pebble screenshot") for c in cmds)


def test_smoke_test_install_failure_skips_screenshot(tmp_path, monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/pebble")
    proj = _project(tmp_path)

    class FailInstall(SmokeRunner):
        def __call__(self, cmd, cwd=None, timeout=120):
            if cmd[:2] == ["pebble", "install"]:
                self.calls.append(cmd)
                return 1, "install error"
            return super().__call__(cmd, cwd, timeout)

    runner = FailInstall()
    res = review.smoke_test(str(proj), runner=runner, sleep=_no_sleep)
    assert res.built is True
    assert res.installed is False
    assert res.launch_shot is None
    assert not any(c.startswith("pebble screenshot") for c in runner.cmds())


def test_smoke_test_gated_without_pebble(tmp_path, monkeypatch):
    from pebble_mcp import devloop

    monkeypatch.setattr(devloop.shutil, "which", lambda _name: None)
    with __import__("pytest").raises(devloop.PebbleUnavailableError):
        review.smoke_test(str(tmp_path / "app"), runner=SmokeRunner())
