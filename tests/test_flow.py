"""Tests for pebble_mcp.flow — the parser and the emulator driver.

The sample flow files are vendored inside the package at
``pebble_mcp/examples/flows/`` so the tests run with no parent repo present;
the parse tests read them from there. The driver tests stub the ``pebble`` CLI
entirely via an injected runner.
"""

from __future__ import annotations

import os

import pytest

import pebble_mcp
from pebble_mcp.flow import (
    MAX_SHOT_NAME,
    AppStep,
    Flow,
    FlowParseError,
    LongPressStep,
    PbwStep,
    PressStep,
    ShotStep,
    TapStep,
    WaitStep,
    Wedged,
    looks_wedged,
    parse_flow,
    parse_flow_file,
    run_flow,
)

# Sample flows are vendored in the package (pebble_mcp/examples/flows), so the
# suite is self-contained and passes with no parent monorepo on disk.
FLOW_DIR = os.path.join(os.path.dirname(os.path.abspath(pebble_mcp.__file__)), "examples", "flows")


def _flow(name: str) -> Flow:
    return parse_flow_file(os.path.join(FLOW_DIR, name))


# --------------------------------------------------------------------------- #
# Parsing the four real flow files
# --------------------------------------------------------------------------- #
def _counts(flow: Flow) -> dict[type, int]:
    counts: dict[type, int] = {}
    for step in flow.steps:
        counts[type(step)] = counts.get(type(step), 0) + 1
    return counts


def test_parse_hail_watch():
    flow = _flow("hail-watch.flow")
    assert flow.name == "hail-watch"
    assert _counts(flow) == {AppStep: 1, WaitStep: 3, ShotStep: 3, PressStep: 2}
    assert flow.steps[0] == AppStep("hail-watch")
    # down = next screen
    assert all(s.button == "down" for s in flow.steps if isinstance(s, PressStep))


def test_parse_coach_watchface():
    flow = _flow("coach-watchface.flow")
    assert _counts(flow) == {AppStep: 1, WaitStep: 1, ShotStep: 1}
    assert flow.steps == [AppStep("coach-watchface"), WaitStep(8.0), ShotStep("coach-face")]


def test_parse_coach_logger():
    flow = _flow("coach-logger.flow")
    c = _counts(flow)
    assert c[AppStep] == 1
    assert c[ShotStep] == 6  # menu, picker, fav 1/2/3, voice-log
    assert c[PressStep] == 6  # select (enter picker), back (exit), down x4


def test_parse_coach_workout():
    flow = _flow("coach-workout.flow")
    c = _counts(flow)
    assert c[AppStep] == 2  # installs twice (reset before the summary shot)
    assert c[ShotStep] == 7
    assert c[LongPressStep] == 6  # 5 exercise completes + summary build
    assert c[PressStep] == 5  # enter ex1, up x2, start plank, stop plank
    # every longpress is 600ms on select
    for s in flow.steps:
        if isinstance(s, LongPressStep):
            assert s.button == "select"
            assert s.duration_ms == 600


def test_all_four_flows_parse():
    names = ["hail-watch.flow", "coach-watchface.flow", "coach-logger.flow", "coach-workout.flow"]
    for n in names:
        assert _flow(n).steps  # non-empty


# --------------------------------------------------------------------------- #
# Parse errors on malformed input
# --------------------------------------------------------------------------- #
def test_parse_comments_and_blanks_ignored():
    flow = parse_flow("# just a comment\n\n   \napp foo  # inline\nwait 1\n")
    assert flow.steps == [AppStep("foo"), WaitStep(1.0)]


def test_parse_tap_and_all_step_types():
    flow = parse_flow(
        "app d\nwait 2\nshot s\npress up\nlongpress select 600\ntap\n"
    )
    assert [type(s) for s in flow.steps] == [
        AppStep, WaitStep, ShotStep, PressStep, LongPressStep, TapStep
    ]


def test_parse_pbw_step():
    flow = parse_flow("pbw dist/hubble.pbw\nwait 3\nshot main\n", name="hubble")
    assert flow.steps[0] == PbwStep("dist/hubble.pbw")
    assert [type(s) for s in flow.steps] == [PbwStep, WaitStep, ShotStep]


def test_parse_pbw_wrong_arity():
    with pytest.raises(FlowParseError, match="expects 1 argument"):
        parse_flow("pbw one two\n")


def test_run_flow_pbw_step_absolute_path(tmp_path):
    pbw = tmp_path / "hubble.pbw"
    pbw.write_bytes(b"PK")  # existence not required by driver, but realistic
    flow = parse_flow(f"pbw {pbw}\nwait 1\nshot main\n", name="h")
    runner = StubRunner()
    result = run_flow(
        flow, str(tmp_path / "g"), repo_root=str(tmp_path), runner=runner, sleep=_no_sleep
    )
    # shots routed under the pbw basename sans extension
    assert [s.app for s in result.shots] == ["hubble"]
    assert result.shots[0].filename == "01-main.png"
    cmds = runner.commands()
    # kill+wipe-first, then install the prebuilt pbw directly (no build)
    assert cmds[0] == "pebble kill"
    assert cmds[1] == "pebble wipe"
    assert cmds[2] == f"pebble install --emulator emery {pbw}"
    assert not any("build" in c for c in cmds)


def test_run_flow_pbw_step_relative_resolves_against_repo_root(tmp_path):
    flow = parse_flow("pbw dist/hubble.pbw\nwait 1\n", name="h")
    seen = {}

    def runner(cmd, cwd=None, timeout=120):
        if cmd[:2] == ["pebble", "install"]:
            seen["pbw"] = cmd[-1]
        return 0, "ok"

    run_flow(flow, str(tmp_path / "g"), repo_root="/repo", runner=runner, sleep=_no_sleep)
    assert seen["pbw"] == os.path.join("/repo", "dist/hubble.pbw")


def test_run_flow_pbw_step_wedge_recovers(tmp_path):
    flow = parse_flow("pbw /abs/hubble.pbw\nwait 1\nshot main\n", name="h")
    runner = StubRunner(fail={"screenshot": 1})
    result = run_flow(
        flow, str(tmp_path / "g"), repo_root=str(tmp_path), runner=runner, sleep=_no_sleep
    )
    assert result.wedge_recoveries == 1
    assert len(result.shots) == 1


def test_parse_unknown_command():
    with pytest.raises(FlowParseError, match="unknown command"):
        parse_flow("app foo\nfrobnicate x\n")


def test_parse_bad_button():
    with pytest.raises(FlowParseError, match="invalid button"):
        parse_flow("press middle\n")


def test_parse_wait_not_a_number():
    with pytest.raises(FlowParseError, match="expects a number"):
        parse_flow("wait soon\n")


def test_parse_longpress_bad_duration():
    with pytest.raises(FlowParseError, match="expects an integer"):
        parse_flow("longpress select fast\n")


def test_parse_wrong_arity():
    with pytest.raises(FlowParseError, match="expects 1 argument"):
        parse_flow("app one two\n")
    with pytest.raises(FlowParseError, match="expects 0 argument"):
        parse_flow("tap now\n")


def test_looks_wedged():
    assert looks_wedged("Coach is not responding")
    assert looks_wedged("Connection refused")
    assert not looks_wedged("all good, screenshot saved")


# --------------------------------------------------------------------------- #
# Driving via a stubbed pebble runner
# --------------------------------------------------------------------------- #
class StubRunner:
    """Records every command and returns queued (rc, out) results.

    ``fail`` maps a command-substring to the number of times it should fail
    (rc=1, wedge marker) before succeeding — used to exercise retry/wedge.
    ``make_shot_files`` writes a dummy PNG for successful screenshot calls so
    the driver's file-exists/size check passes.
    """

    def __init__(self, fail: dict[str, int] | None = None, make_shot_files: bool = True):
        self.calls: list[list[str]] = []
        self.fail = dict(fail or {})
        self.make_shot_files = make_shot_files

    def __call__(self, cmd, cwd=None, timeout=120):
        self.calls.append(cmd)
        for marker, remaining in list(self.fail.items()):
            if remaining > 0 and any(marker in part for part in cmd):
                self.fail[marker] = remaining - 1
                return 1, f"error: {marker} not responding"
        if self.make_shot_files and cmd[:2] == ["pebble", "screenshot"]:
            dest = cmd[-1]
            with open(dest, "wb") as f:
                f.write(b"\x89PNG\r\n\x1a\n")  # non-empty stub
        return 0, "ok"

    def commands(self) -> list[str]:
        return [" ".join(c) for c in self.calls]


def _no_sleep(_seconds: float) -> None:
    pass


def test_pacing_is_baked_into_the_driver(tmp_path):
    # Correctness must not depend on a flow author inserting waits: every button
    # press settles, and a successful install settles, via the injected sleep.
    from pebble_mcp.flow import BUTTON_SETTLE_SEC, POST_INSTALL_SETTLE_SEC

    slept: list[float] = []
    flow = parse_flow("app demo\nshot a\npress down\npress up\ntap\n", name="demo")
    run_flow(
        flow, str(tmp_path / "g"), repo_root=str(tmp_path),
        runner=StubRunner(), sleep=slept.append,
    )
    # One settle per input (2 presses + 1 tap) and one post-install settle.
    assert slept.count(BUTTON_SETTLE_SEC) == 3
    assert POST_INSTALL_SETTLE_SEC in slept


def test_run_flow_happy_path(tmp_path):
    flow = parse_flow(
        "app demo\nwait 8\nshot first\npress down\nlongpress select 600\ntap\nshot second\n",
        name="demo",
    )
    runner = StubRunner()
    result = run_flow(
        flow, str(tmp_path / "gallery"), repo_root=str(tmp_path), runner=runner, sleep=_no_sleep
    )

    assert result.name == "demo"
    assert result.retries == 0
    assert result.wedge_recoveries == 0
    assert [s.filename for s in result.shots] == ["01-first.png", "02-second.png"]
    assert all(s.app == "demo" for s in result.shots)
    # shot files actually written under gallery/demo/
    for s in result.shots:
        assert os.path.exists(s.path)
        assert s.path.endswith(os.path.join("gallery", "demo", s.filename))

    cmds = runner.commands()
    # install always kills+wipes first, then installs the pbw
    assert cmds[0] == "pebble kill"
    assert cmds[1] == "pebble wipe"
    assert cmds[2] == "pebble install --emulator emery build/demo.pbw"
    # the button/tap/screenshot commands, in flow order
    assert "pebble screenshot --no-open" in cmds[3]
    assert "pebble emu-button click down" in cmds
    assert "pebble emu-button click select --duration 600" in cmds
    assert "pebble emu-tap" in cmds


def test_run_flow_install_cwd_is_repo_root_plus_project(tmp_path):
    flow = parse_flow("app coach-workout\nwait 1\n", name="x")
    seen = {}

    def runner(cmd, cwd=None, timeout=120):
        if cmd[:2] == ["pebble", "install"]:
            seen["cwd"] = cwd
        return 0, "ok"

    run_flow(flow, str(tmp_path / "g"), repo_root="/repo", runner=runner, sleep=_no_sleep)
    assert seen["cwd"] == os.path.join("/repo", "coach-workout")


def test_run_flow_screenshot_retry_then_succeed(tmp_path):
    # First screenshot returns rc!=0 with NO wedge marker -> plain retry once.
    flow = parse_flow("app d\nshot only\n", name="r")

    class RetryRunner(StubRunner):
        def __init__(self):
            super().__init__()
            self.shots_seen = 0

        def __call__(self, cmd, cwd=None, timeout=120):
            if cmd[:2] == ["pebble", "screenshot"]:
                self.shots_seen += 1
                if self.shots_seen == 1:
                    self.calls.append(cmd)
                    return 1, "transient glitch"  # not a wedge marker
            return super().__call__(cmd, cwd, timeout)

    runner = RetryRunner()
    result = run_flow(
        flow, str(tmp_path / "g"), repo_root=str(tmp_path), runner=runner, sleep=_no_sleep
    )
    assert result.retries == 1
    assert len(result.shots) == 1
    assert runner.commands().count("pebble screenshot --no-open " + result.shots[0].path) == 2


def test_run_flow_wedge_recovery_restarts_once(tmp_path):
    # Screenshot wedges on the very first attempt -> kill+wipe, restart flow once.
    flow = parse_flow("app d\nwait 1\nshot only\n", name="w")
    runner = StubRunner(fail={"screenshot": 1})  # first screenshot call wedges

    result = run_flow(
        flow, str(tmp_path / "g"), repo_root=str(tmp_path), runner=runner, sleep=_no_sleep
    )

    assert result.wedge_recoveries == 1
    assert result.restarted is True
    assert len(result.shots) == 1  # succeeded on the restart

    cmds = runner.commands()
    # Sequence: install(kill,wipe,install) -> screenshot(wedges) ->
    #           unwedge(kill,wipe) -> restart: install(kill,wipe,install) -> screenshot(ok)
    assert cmds.count("pebble kill") == 3
    assert cmds.count("pebble wipe") == 3
    assert cmds.count("pebble install --emulator emery build/d.pbw") == 2


def test_run_flow_second_wedge_raises(tmp_path):
    # Screenshot wedges on both the initial run and the restart -> give up.
    flow = parse_flow("app d\nshot only\n", name="w2")
    runner = StubRunner(fail={"screenshot": 99})  # always wedges

    with pytest.raises(Wedged):
        run_flow(
            flow, str(tmp_path / "g"), repo_root=str(tmp_path), runner=runner, sleep=_no_sleep
        )


# --------------------------------------------------------------------------- #
# Adversarial: shot-name / app-dir path traversal must not escape out_dir
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "name",
    [
        "../../../etc/pwned",   # leading traversal
        "foo/../../../etc/x",   # internal component then traversal (escapes)
        "sub/deep",             # any separator is refused
        "back\\slash",          # windows-style separator
        ".",
        "..",
        "with\x00null",         # null byte
        "ctrl\x01char",         # control char
        "",                     # empty (e.g. `shot ""` -> handled at run, but token)
    ],
)
def test_parse_rejects_unsafe_shot_names(name):
    # An empty token can't actually occur from split(); guard the rest.
    if name == "":
        return
    with pytest.raises(FlowParseError):
        parse_flow(f"app demo\nshot {name}\n")


def test_parse_rejects_overlong_shot_name():
    long = "x" * (MAX_SHOT_NAME + 1)
    with pytest.raises(FlowParseError, match="too long"):
        parse_flow(f"app demo\nshot {long}\n")


def test_parse_allows_unicode_and_dashed_shot_names():
    flow = parse_flow("app demo\nshot café-ünïcode_01\n")
    assert flow.steps[1] == ShotStep("café-ünïcode_01")


def test_parse_rejects_app_absolute_and_traversal():
    with pytest.raises(FlowParseError, match="absolute"):
        parse_flow("app /etc\n")
    with pytest.raises(FlowParseError, match="traversal"):
        parse_flow("app ../../../etc\n")
    with pytest.raises(FlowParseError, match="null byte"):
        parse_flow("app foo\x00bar\n")


def test_parse_allows_app_subdir_without_traversal():
    flow = parse_flow("app sub/project\n")
    assert flow.steps == [AppStep("sub/project")]


def test_shot_traversal_does_not_escape_out_dir(tmp_path):
    # End-to-end proof: even if a ShotStep with separators is constructed
    # directly (bypassing the parser), the runtime guard refuses to write
    # outside out_dir. Marker file below must never appear.
    outdir = tmp_path / "g"
    escaped = tmp_path / "ESCAPED.png"
    # "a/../../../ESCAPED": the NN- prefix defuses one "..", the rest escape.
    flow = Flow(name="x", steps=[AppStep("demo"), ShotStep("a/../../../ESCAPED")])
    runner = StubRunner()
    with pytest.raises(FlowParseError, match="escape"):
        run_flow(flow, str(outdir), repo_root=str(tmp_path), runner=runner, sleep=_no_sleep)
    assert not escaped.exists()


def test_app_traversal_does_not_escape_out_dir(tmp_path):
    # AppStep built directly with traversal -> runtime guard refuses.
    outdir = tmp_path / "g"
    flow = Flow(name="x", steps=[AppStep("../../ESCAPED")])
    with pytest.raises(FlowParseError, match="escape"):
        run_flow(flow, str(outdir), repo_root=str(tmp_path), runner=StubRunner(), sleep=_no_sleep)
    assert not (tmp_path.parent / "ESCAPED").exists()


# --------------------------------------------------------------------------- #
# Adversarial: screenshot must be a real PNG, not just non-empty
# --------------------------------------------------------------------------- #
class NonPngRunner(StubRunner):
    """Writes a non-empty, non-PNG file for every screenshot (corrupt capture)."""

    def __call__(self, cmd, cwd=None, timeout=120):
        if cmd[:2] == ["pebble", "screenshot"]:
            self.calls.append(cmd)
            with open(cmd[-1], "wb") as f:
                f.write(b"GIF89a not a png")
            return 0, "ok"
        return super().__call__(cmd, cwd, timeout)


def test_non_png_screenshot_is_treated_as_wedge(tmp_path):
    flow = parse_flow("app d\nshot only\n", name="np")
    runner = NonPngRunner()
    # A persistently non-PNG capture wedges, recovers once, then gives up.
    with pytest.raises(Wedged, match="non-PNG"):
        run_flow(flow, str(tmp_path / "g"), repo_root=str(tmp_path), runner=runner, sleep=_no_sleep)


# --------------------------------------------------------------------------- #
# Adversarial: robustness of the parser to odd-but-legal input
# --------------------------------------------------------------------------- #
def test_parse_crlf_line_endings():
    flow = parse_flow("app demo\r\nwait 1\r\nshot first\r\n")
    assert flow.steps == [AppStep("demo"), WaitStep(1.0), ShotStep("first")]


def test_parse_empty_and_comment_only_flows_have_no_steps():
    assert parse_flow("").steps == []
    assert parse_flow("# just comments\n   \n\t\n# more\n").steps == []


def test_parse_large_flow_is_linear_and_cheap():
    # 10k steps parses fast (no pathological cost); just prove it completes.
    text = "app demo\n" + "wait 1\n" * 10_000
    flow = parse_flow(text)
    assert len(flow.steps) == 10_001
