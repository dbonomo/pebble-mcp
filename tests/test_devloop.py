"""Tests for pebble_mcp.devloop — the Tier-3 dev-loop wrappers.

The ``pebble`` CLI is stubbed entirely via an injected runner; the waf output
fixtures below are trimmed from real ``pebble build`` runs in this repo so the
parser is exercised against realistic text. PATH-gating is exercised by
monkeypatching ``shutil.which``.
"""

from __future__ import annotations

import os

import pytest

from pebble_mcp import devloop
from pebble_mcp.devloop import (
    BuildResult,
    Diagnostic,
    PebbleUnavailableError,
    build,
    emu_start,
    emu_stop,
    install,
    logs_capture,
    parse_build_output,
)

# --------------------------------------------------------------------------- #
# Realistic waf output fixtures (trimmed from real `pebble build` runs)
# --------------------------------------------------------------------------- #
WAF_SUCCESS = """\
'configure' finished successfully (0.081s)
Waf: Entering directory `/home/x/hail-watch/build'
[13/16] Compiling emery | c: src/c/hail-watch.c -> build/src/c/hail-watch.c.9.o
[15/16] Linking emery | cprogram: build/src/c/hail-watch.c.9.o -> build/emery/pebble-app.elf
/opt/tc/arm-none-eabi/bin/ld: warning: pebble-app.elf has a LOAD segment with RWX permissions
[20/20] Creating app_bundle:  -> build/hail-watch.pbw
Waf: Leaving directory `/home/x/hail-watch/build'
'build' finished successfully (0.614s)
"""

WAF_ERROR = """\
'configure' finished successfully (0.031s)
Waf: Entering directory `/home/x/hail-watch/build'
[13/16] Compiling emery | c: ../src/c/hail-watch.c -> build/src/c/hail-watch.c.9.o
../src/c/hail-watch.c:389:21: error: 'undefined_symbol_here' undeclared (first use in this function)
../src/c/hail-watch.c:389:51: error: 'notdeclared' undeclared (first use in this function)
../src/c/hail-watch.c:389:64: error: control reaches end of non-void function [-Werror=return-type]
cc1: all warnings being treated as errors
Build failed
Build failed.
"""

WAF_WARNING_ONLY = """\
Waf: Entering directory `/home/x/app/build'
../src/c/app.c:42:9: warning: unused variable 'foo' [-Wunused-variable]
[20/20] Creating app_bundle:  -> build/app.pbw
'build' finished successfully (0.2s)
"""


# --------------------------------------------------------------------------- #
# parse_build_output
# --------------------------------------------------------------------------- #
def test_parse_error_diagnostics_with_file_line_col():
    diags = parse_build_output(WAF_ERROR)
    errs = [d for d in diags if d.severity == "error"]
    # 3 gcc errors + collect2/"Build failed" is not a colon-diag; cc1 line has no colon-sev
    file_errs = [d for d in errs if d.file]
    assert len(file_errs) == 3
    first = file_errs[0]
    assert first.file == "src/c/hail-watch.c"  # leading ../ stripped
    assert first.line == 389
    assert first.col == 21
    assert "undefined_symbol_here" in first.message
    # the -Werror=return-type message is preserved
    assert any("non-void function" in d.message for d in file_errs)


def test_parse_warning_with_flag():
    diags = parse_build_output(WAF_WARNING_ONLY)
    warns = [d for d in diags if d.severity == "warning"]
    assert len(warns) == 1
    assert warns[0].file == "src/c/app.c"
    assert warns[0].line == 42
    assert warns[0].col == 9
    assert "unused variable" in warns[0].message


def test_parse_fileless_linker_warning():
    diags = parse_build_output(WAF_SUCCESS)
    warns = [d for d in diags if d.severity == "warning"]
    # the ld RWX-permissions warning has no file:line -> file is None
    assert len(warns) == 1
    assert warns[0].file is None
    assert "RWX permissions" in warns[0].message


def test_parse_clean_output_has_no_diagnostics():
    clean = "[20/20] Creating app_bundle:  -> build/app.pbw\n'build' finished successfully (0.1s)\n"
    assert parse_build_output(clean) == []


def test_parse_strips_ansi_colour_codes():
    # gcc/waf colourised output wraps the tokens in CSI escapes; without
    # stripping, the diagnostic regexes match nothing at all.
    colored = (
        "\x1b[01m\x1b[K../src/app.c:5:3:\x1b[m\x1b[K "
        "\x1b[01;31m\x1b[Kerror:\x1b[m\x1b[K boom"
    )
    diags = parse_build_output(colored)
    assert len(diags) == 1
    assert diags[0].severity == "error"
    assert diags[0].file == "src/app.c"
    assert diags[0].line == 5
    assert diags[0].col == 3
    assert diags[0].message == "boom"


# --------------------------------------------------------------------------- #
# build()
# --------------------------------------------------------------------------- #
class Recorder:
    """Runner stub returning a canned (rc, out) and recording commands."""

    def __init__(self, rc: int = 0, out: str = "ok", *, per_cmd=None):
        self.rc = rc
        self.out = out
        self.per_cmd = per_cmd or {}
        self.calls: list[list[str]] = []

    def __call__(self, cmd, cwd=None, timeout=120):
        self.calls.append(cmd)
        for key, val in self.per_cmd.items():
            if key in " ".join(cmd):
                return val
        return self.rc, self.out

    def cmds(self):
        return [" ".join(c) for c in self.calls]


def test_build_success_parses_pbw_and_flags(tmp_path):
    proj = tmp_path / "hail-watch"
    (proj / "build").mkdir(parents=True)
    (proj / "build" / "hail-watch.pbw").write_bytes(b"PK")
    runner = Recorder(0, WAF_SUCCESS)
    res = build(str(proj), runner=runner)
    assert res.success is True
    assert res.errors == []
    assert len(res.warnings) == 1  # the ld RWX warning
    assert res.pbw_path is not None
    assert res.pbw_path.endswith(os.path.join("hail-watch", "build", "hail-watch.pbw"))
    assert res.pbw_path == os.path.abspath(res.pbw_path)
    assert runner.cmds() == ["pebble build"]


def test_build_failure_reports_errors_and_no_pbw(tmp_path):
    runner = Recorder(1, WAF_ERROR)
    res = build(str(tmp_path), runner=runner)
    assert res.success is False
    assert len(res.errors) == 3
    assert res.pbw_path is None
    assert res.returncode == 1


def test_build_pbw_glob_fallback_when_output_lacks_path(tmp_path):
    proj = tmp_path / "app"
    (proj / "build").mkdir(parents=True)
    (proj / "build" / "app.pbw").write_bytes(b"PK")
    out = "'build' finished successfully (0.1s)\n"  # no "-> ....pbw" line
    res = build(str(proj), runner=Recorder(0, out))
    assert res.success is True
    assert res.pbw_path.endswith(os.path.join("app", "build", "app.pbw"))


# --------------------------------------------------------------------------- #
# install()
# --------------------------------------------------------------------------- #
def _no_sleep(_s):
    pass


def test_install_from_prebuilt_pbw_kill_wipe_then_install(tmp_path):
    pbw = tmp_path / "hubble.pbw"
    pbw.write_bytes(b"PK")
    runner = Recorder(0, "ok")
    res = install(str(pbw), runner=runner, sleep=_no_sleep)
    assert res.success is True
    assert res.build_result is None  # no build for a prebuilt pbw
    cmds = runner.cmds()
    assert cmds[0] == "pebble kill"
    assert cmds[1] == "pebble wipe"
    assert cmds[2] == f"pebble install --emulator emery {os.path.abspath(str(pbw))}"
    assert not any("pebble build" in c for c in cmds)


def test_install_missing_pbw_file_fails_without_touching_emulator(tmp_path):
    res = install(str(tmp_path / "nope.pbw"), runner=Recorder(0, "ok"), sleep=_no_sleep)
    assert res.success is False
    assert "no such .pbw" in res.output


def test_install_from_project_builds_then_installs(tmp_path):
    proj = tmp_path / "app"
    (proj / "build").mkdir(parents=True)
    (proj / "build" / "app.pbw").write_bytes(b"PK")
    # build -> success output; install -> ok
    runner = Recorder(per_cmd={"pebble build": (0, WAF_WARNING_ONLY.replace("app.pbw", "app.pbw"))})
    # ensure the emitted pbw path in output resolves to the real file
    res = install(str(proj), runner=runner, sleep=_no_sleep)
    assert res.success is True
    assert res.build_result is not None
    assert res.build_result.success is True
    cmds = runner.cmds()
    assert cmds[0] == "pebble build"
    assert cmds[1] == "pebble kill"
    assert cmds[2] == "pebble wipe"
    assert cmds[3].startswith("pebble install --emulator emery ")
    assert cmds[3].endswith(os.path.join("app", "build", "app.pbw"))


def test_install_from_project_build_failure_short_circuits(tmp_path):
    runner = Recorder(per_cmd={"pebble build": (1, WAF_ERROR)})
    res = install(str(tmp_path / "app"), runner=runner, sleep=_no_sleep)
    assert res.success is False
    assert res.build_result is not None
    assert res.build_result.success is False
    # never reached the emulator
    assert runner.cmds() == ["pebble build"]


def test_install_platform_flag_respected(tmp_path):
    pbw = tmp_path / "x.pbw"
    pbw.write_bytes(b"PK")
    runner = Recorder(0, "ok")
    install(str(pbw), platform="basalt", runner=runner, sleep=_no_sleep)
    assert any("--emulator basalt" in c for c in runner.cmds())


# --------------------------------------------------------------------------- #
# emulator lifecycle
# --------------------------------------------------------------------------- #
def test_emu_start_boots_via_emu_battery():
    runner = Recorder(0, "")
    res = emu_start(runner=runner)
    assert res.ok is True
    assert runner.cmds() == ["pebble emu-battery --emulator emery --percent 100"]


def test_emu_stop_kill_only():
    runner = Recorder(0, "")
    res = emu_stop(runner=runner)
    assert res.wiped is False
    assert runner.cmds() == ["pebble kill"]


def test_emu_stop_with_wipe():
    runner = Recorder(0, "")
    res = emu_stop(wipe=True, runner=runner)
    assert res.wiped is True
    assert runner.cmds() == ["pebble kill", "pebble wipe"]


# --------------------------------------------------------------------------- #
# logs_capture bounding
# --------------------------------------------------------------------------- #
def test_logs_capture_seconds_bounds_the_timeout():
    seen = {}

    def runner(cmd, cwd=None, timeout=120):
        seen["timeout"] = timeout
        seen["cmd"] = cmd
        return 124, "line1\nline2\n"  # 124 = timed-out stream, expected

    res = logs_capture(seconds=7, runner=runner)
    assert seen["timeout"] == 7
    assert seen["cmd"] == ["pebble", "logs", "--emulator", "emery"]
    assert res.text == "line1\nline2\n"


def test_logs_capture_until_pattern_cuts_at_first_match():
    out = "boot\nAPP_READY here\nmore\nnoise\n"
    res = logs_capture(until_pattern="APP_READY", runner=Recorder(0, out))
    assert res.matched is True
    assert res.text == "boot\nAPP_READY here\n"


def test_logs_capture_until_pattern_absent():
    out = "boot\nrunning\n"
    res = logs_capture(until_pattern="NEVER", runner=Recorder(0, out))
    assert res.matched is False
    assert res.text == out


def test_logs_capture_max_bytes_clamps_tail():
    big = "x" * 5000 + "END"
    res = logs_capture(max_bytes=100, runner=Recorder(0, big))
    assert res.truncated is True
    assert len(res.text.encode("utf-8")) <= 100
    assert res.text.endswith("END")  # keeps the tail


def test_logs_capture_huge_seconds_clamped_to_ceiling():
    from pebble_mcp.devloop import MAX_LOG_SECONDS

    seen = {}

    def runner(cmd, cwd=None, timeout=120):
        seen["timeout"] = timeout
        return 0, "x"

    logs_capture(seconds=10**9, runner=runner)
    assert seen["timeout"] == MAX_LOG_SECONDS


def test_logs_capture_non_finite_seconds_do_not_crash():
    from pebble_mcp.devloop import MAX_LOG_SECONDS

    seen = {}

    def runner(cmd, cwd=None, timeout=120):
        seen["timeout"] = timeout
        return 0, "x"

    # inf/nan previously raised OverflowError from int(); now clamps to ceiling.
    logs_capture(seconds=float("inf"), runner=runner)
    assert seen["timeout"] == MAX_LOG_SECONDS
    logs_capture(seconds=float("nan"), runner=runner)
    assert seen["timeout"] == MAX_LOG_SECONDS


def test_logs_capture_zero_or_negative_max_bytes_does_not_return_everything():
    big = "x" * 5000 + "END"
    # max_bytes=0 previously returned the whole buffer (encoded[-0:]); now bounded.
    r0 = logs_capture(max_bytes=0, runner=Recorder(0, big))
    assert len(r0.text.encode("utf-8")) <= 1
    assert r0.truncated is True
    # negative previously produced a wrong-direction slice; now bounded too.
    rn = logs_capture(max_bytes=-100, runner=Recorder(0, big))
    assert len(rn.text.encode("utf-8")) <= 1


# --------------------------------------------------------------------------- #
# PATH gating
# --------------------------------------------------------------------------- #
@pytest.fixture
def no_pebble(monkeypatch):
    monkeypatch.setattr(devloop.shutil, "which", lambda _name: None)


def test_gating_build(no_pebble):
    with pytest.raises(PebbleUnavailableError, match="not found on PATH"):
        build("anything")


def test_gating_install(no_pebble):
    with pytest.raises(PebbleUnavailableError):
        install("anything.pbw")


def test_gating_emu_start(no_pebble):
    with pytest.raises(PebbleUnavailableError):
        emu_start()


def test_gating_emu_stop(no_pebble):
    with pytest.raises(PebbleUnavailableError):
        emu_stop()


def test_gating_logs(no_pebble):
    with pytest.raises(PebbleUnavailableError):
        logs_capture(seconds=1)


def test_pebble_available_reflects_which(monkeypatch):
    monkeypatch.setattr(devloop.shutil, "which", lambda _name: "/usr/bin/pebble")
    assert devloop.pebble_available() is True
    monkeypatch.setattr(devloop.shutil, "which", lambda _name: None)
    assert devloop.pebble_available() is False


# keep imports used (BuildResult/Diagnostic are part of the public API surface)
def test_public_dataclasses_constructible():
    d = Diagnostic(severity="error", message="m", file="f.c", line=1, col=2, raw="r")
    assert d.severity == "error"
    b = BuildResult(success=True, project_dir="/p", returncode=0)
    assert b.errors == [] and b.warnings == []
