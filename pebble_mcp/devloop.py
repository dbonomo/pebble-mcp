"""Dev-loop wrapper library — build / emulator / install / logs over ``pebble``.

This is the Tier-3 core (roadmap task C1): thin, testable functions that drive
the ``pebble`` CLI for the local development loop. Everything here runs through
the *same* injectable :data:`~pebble_mcp.flow.PebbleRunner` used by the flow
engine, so there is exactly one subprocess layer in the package and tests can
stub the CLI entirely.

What lives here:

* :func:`build` — build a project and parse waf output into structured
  :class:`Diagnostic` errors/warnings (with ``file:line`` where the compiler
  gives it), a ``success`` flag, and the produced ``.pbw`` path.
* :func:`emu_start` / :func:`emu_stop` — boot / kill the emery emulator
  (``emu_stop`` optionally wipes).
* :func:`install` — install onto the emulator, always kill+wipe-first. Accepts
  **either** a project directory (build-if-needed, then install the fresh
  ``.pbw``) **or** a path to a prebuilt ``.pbw`` (install it directly).
* :func:`logs_capture` — bounded log capture (by seconds and/or until a
  pattern), never an unbounded firehose.

Every function is gated on ``shutil.which("pebble")`` and raises
:class:`PebbleUnavailableError` with an actionable message when the CLI is
absent — MCP tool wiring (task C3) can surface that directly.
"""

from __future__ import annotations

import glob
import math
import os
import re
import shutil
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from .flow import PebbleRunner, looks_wedged, subprocess_runner

# The single emulator this repo targets; other platforms are accepted but emery
# is the default everywhere (matches the flow engine and platforms table).
DEFAULT_PLATFORM = "emery"

# Hard ceiling on a single bounded log capture. ``pebble logs`` streams forever,
# so the capture ``seconds`` is the subprocess timeout; without a ceiling a
# caller passing a huge (or non-finite) value would block the emulator for
# effectively ever. Five minutes is far more than any real "watch it boot" need.
MAX_LOG_SECONDS = 300


# --------------------------------------------------------------------------- #
# Gating
# --------------------------------------------------------------------------- #
class PebbleUnavailableError(RuntimeError):
    """Raised when a Tier-3 operation is attempted but ``pebble`` is not on PATH."""


def pebble_available() -> bool:
    """True if the ``pebble`` CLI resolves on PATH (Tier-3 capability check)."""
    return shutil.which("pebble") is not None


def _require_pebble() -> None:
    if not pebble_available():
        raise PebbleUnavailableError(
            "the 'pebble' CLI was not found on PATH; install the Pebble SDK "
            "tool (`uv tool install pebble-tool`) and ensure ~/.local/bin is on "
            "PATH to use the dev-loop (Tier-3) functions"
        )


# --------------------------------------------------------------------------- #
# Build-output parsing
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Diagnostic:
    """One compiler/linker diagnostic parsed from waf output.

    ``file``/``line``/``col`` are populated when the underlying gcc message
    carries them (``path:line:col: severity: message``); linker and driver
    messages (e.g. ``collect2: error: ...``) land with ``file=None``.
    """

    severity: str  # "error" | "warning"
    message: str
    file: str | None = None
    line: int | None = None
    col: int | None = None
    raw: str = ""


# gcc/clang style: "path/to/file.c:12:34: warning: msg [-Wflag]" (col optional).
_GCC_DIAG_RE = re.compile(
    r"^\s*(?P<file>[^\s:][^:]*?):(?P<line>\d+)(?::(?P<col>\d+))?:\s*"
    r"(?P<sev>fatal error|error|warning):\s*(?P<msg>.*)$"
)
# Fallback for diagnostics without a file:line (linker, cc1, collect2, ld).
_GENERIC_DIAG_RE = re.compile(r"(?:^|\s)(?P<sev>error|warning):\s*(?P<msg>.+)$")

# "[20/20] Creating app_bundle:  -> build/my-app.pbw"
_PBW_RE = re.compile(r"->\s*(?P<pbw>\S+\.pbw)\b")

# ANSI CSI escape sequences (colours, cursor moves). gcc/waf emit these when
# they think stdout is a TTY (or CLICOLOR_FORCE is set); left in place they wrap
# the ``file:line: severity:`` tokens in escapes and defeat the diagnostic
# regexes entirely. Strip them per line before matching.
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")

_BUILD_OK_MARKER = "'build' finished successfully"
_BUILD_FAIL_MARKER = "Build failed"


def _clean_file(path: str) -> str:
    """Strip leading ``../`` hops so a build-relative path reads project-relative.

    waf compiles inside ``build/<platform>/`` so gcc reports sources as
    ``../src/c/app.c``; collapsing the leading ``../`` gives ``src/c/app.c``.
    """
    while path.startswith("../"):
        path = path[3:]
    return path


def parse_build_output(output: str) -> list[Diagnostic]:
    """Parse waf/gcc ``output`` into an ordered list of :class:`Diagnostic`.

    Recognises gcc ``file:line:col: severity: message`` lines (the common,
    actionable case) and falls back to file-less ``severity: message`` lines
    for linker/driver diagnostics. Order is preserved; nothing is deduplicated.
    """
    diags: list[Diagnostic] = []
    for raw in output.splitlines():
        line = _ANSI_RE.sub("", raw).rstrip()
        m = _GCC_DIAG_RE.match(line)
        if m:
            sev = "error" if m.group("sev") in ("error", "fatal error") else "warning"
            diags.append(
                Diagnostic(
                    severity=sev,
                    message=m.group("msg").strip(),
                    file=_clean_file(m.group("file").strip()),
                    line=int(m.group("line")),
                    col=int(m.group("col")) if m.group("col") else None,
                    raw=line,
                )
            )
            continue
        g = _GENERIC_DIAG_RE.search(line)
        if g:
            diags.append(
                Diagnostic(
                    severity=g.group("sev"),
                    message=g.group("msg").strip(),
                    raw=line,
                )
            )
    return diags


@dataclass
class BuildResult:
    """Outcome of a :func:`build` call."""

    success: bool
    project_dir: str
    returncode: int
    errors: list[Diagnostic] = field(default_factory=list)
    warnings: list[Diagnostic] = field(default_factory=list)
    pbw_path: str | None = None
    output: str = ""


def _find_pbw(output: str, project_dir: str) -> str | None:
    """Resolve the produced ``.pbw`` from build output, falling back to a glob."""
    matches = _PBW_RE.findall(output)
    if matches:
        candidate = matches[-1]
        if not os.path.isabs(candidate):
            candidate = os.path.join(project_dir, candidate)
        if os.path.exists(candidate):
            return os.path.abspath(candidate)
    # Fallback: newest .pbw under build/ (output parsing can miss on odd layouts).
    built = glob.glob(os.path.join(project_dir, "build", "*.pbw"))
    if built:
        return os.path.abspath(max(built, key=os.path.getmtime))
    return None


def build(
    project_dir: str,
    *,
    runner: PebbleRunner = subprocess_runner,
    timeout: int = 300,
) -> BuildResult:
    """Build the Pebble project at ``project_dir`` and structure the result.

    Runs ``pebble build`` in ``project_dir``. ``success`` is true when the CLI
    exits 0 and waf prints its success marker (and no ``Build failed``).
    ``errors``/``warnings`` come from :func:`parse_build_output`; ``pbw_path``
    is the produced bundle (absolute) on success.
    """
    _require_pebble()
    rc, out = runner(["pebble", "build"], project_dir, timeout)
    diags = parse_build_output(out)
    errors = [d for d in diags if d.severity == "error"]
    warnings = [d for d in diags if d.severity == "warning"]
    success = rc == 0 and _BUILD_OK_MARKER in out and _BUILD_FAIL_MARKER not in out
    pbw = _find_pbw(out, project_dir) if success else None
    return BuildResult(
        success=success,
        project_dir=os.path.abspath(project_dir),
        returncode=rc,
        errors=errors,
        warnings=warnings,
        pbw_path=pbw,
        output=out,
    )


# --------------------------------------------------------------------------- #
# Emulator lifecycle
# --------------------------------------------------------------------------- #
@dataclass
class EmuResult:
    """Outcome of an emulator lifecycle call."""

    ok: bool
    platform: str
    returncode: int
    output: str = ""
    wiped: bool = False


def emu_start(
    platform: str = DEFAULT_PLATFORM,
    *,
    runner: PebbleRunner = subprocess_runner,
    timeout: int = 120,
) -> EmuResult:
    """Boot the emulator for ``platform`` (default emery).

    pebble-tool has no bare "boot" verb — the emulator launches as a side
    effect of the first command that targets it. We issue a benign
    ``emu-battery`` set, which boots the emulator and returns, so callers get
    an explicit, deterministic start step.
    """
    _require_pebble()
    rc, out = runner(
        ["pebble", "emu-battery", "--emulator", platform, "--percent", "100"],
        None,
        timeout,
    )
    return EmuResult(ok=rc == 0, platform=platform, returncode=rc, output=out)


def emu_stop(
    wipe: bool = False,
    *,
    runner: PebbleRunner = subprocess_runner,
    timeout: int = 60,
) -> EmuResult:
    """Kill running emulators; when ``wipe`` is set, also wipe their data."""
    _require_pebble()
    rc, out = runner(["pebble", "kill"], None, timeout)
    ok = rc == 0
    if wipe:
        wrc, wout = runner(["pebble", "wipe"], None, timeout)
        ok = ok and wrc == 0
        rc = rc or wrc
        out = out + "\n" + wout
    return EmuResult(ok=ok, platform=DEFAULT_PLATFORM, returncode=rc, output=out, wiped=wipe)


# --------------------------------------------------------------------------- #
# Install
# --------------------------------------------------------------------------- #
@dataclass
class InstallResult:
    """Outcome of an :func:`install` call."""

    success: bool
    platform: str
    pbw_path: str | None
    returncode: int
    output: str = ""
    build_result: BuildResult | None = None


def _looks_like_pbw(target: str) -> bool:
    return target.endswith(".pbw")


def _kill_wipe(runner: PebbleRunner, sleep: Callable[[float], None]) -> None:
    # The kill+wipe-before-install lesson (see flow._Driver.install_app): a
    # clean slate is what makes a freshly installed app actually launch and
    # avoids the "not responding" wedge from installing over a running emu.
    runner(["pebble", "kill"], None, 60)
    runner(["pebble", "wipe"], None, 60)
    sleep(2)


def _install_pbw(
    pbw_path: str,
    platform: str,
    runner: PebbleRunner,
    sleep: Callable[[float], None],
    timeout: int,
) -> tuple[int, str]:
    _kill_wipe(runner, sleep)
    rc, out = -1, ""
    for attempt in range(2):
        rc, out = runner(["pebble", "install", "--emulator", platform, pbw_path], None, timeout)
        if rc == 0:
            return rc, out
        if looks_wedged(out) or attempt == 1:
            return rc, out
        sleep(2)
    return rc, out


def install(
    target: str,
    platform: str = DEFAULT_PLATFORM,
    *,
    runner: PebbleRunner = subprocess_runner,
    sleep: Callable[[float], None] = time.sleep,
    build_timeout: int = 300,
    install_timeout: int = 180,
) -> InstallResult:
    """Install ``target`` onto the emulator, kill+wipe-first.

    ``target`` is either:

    * a **path to a ``.pbw``** — installed directly, or
    * a **project directory** — built first (via :func:`build`); the resulting
      ``.pbw`` is then installed. A failed build short-circuits and returns the
      :class:`BuildResult` without touching the emulator.

    Either way the emulator is killed and wiped before the install command, so
    the app actually launches instead of hiding behind a persisted face.
    """
    _require_pebble()

    build_result: BuildResult | None = None
    if _looks_like_pbw(target):
        pbw_path = os.path.abspath(target)
        if not os.path.isfile(pbw_path):
            return InstallResult(
                success=False,
                platform=platform,
                pbw_path=pbw_path,
                returncode=2,
                output=f"no such .pbw file: {pbw_path}",
            )
    else:
        build_result = build(target, runner=runner, timeout=build_timeout)
        if not build_result.success or not build_result.pbw_path:
            return InstallResult(
                success=False,
                platform=platform,
                pbw_path=build_result.pbw_path,
                returncode=build_result.returncode or 1,
                output=build_result.output,
                build_result=build_result,
            )
        pbw_path = build_result.pbw_path

    rc, out = _install_pbw(pbw_path, platform, runner, sleep, install_timeout)
    return InstallResult(
        success=rc == 0,
        platform=platform,
        pbw_path=pbw_path,
        returncode=rc,
        output=out,
        build_result=build_result,
    )


# --------------------------------------------------------------------------- #
# Logs (bounded)
# --------------------------------------------------------------------------- #
@dataclass
class LogsResult:
    """Bounded log capture result."""

    text: str
    returncode: int
    truncated: bool = False
    matched: bool = False


def logs_capture(
    *,
    seconds: float = 10.0,
    until_pattern: str | None = None,
    max_bytes: int = 64 * 1024,
    platform: str = DEFAULT_PLATFORM,
    runner: PebbleRunner = subprocess_runner,
) -> LogsResult:
    """Capture emulator logs, bounded three ways — never a firehose.

    ``pebble logs`` streams forever, so capture is always bounded by
    ``seconds`` (the CLI is run under that timeout and then stops). When
    ``until_pattern`` is given, the captured text is additionally cut at the
    first line containing that substring (``matched=True``). The returned text
    is finally clamped to the last ``max_bytes`` bytes (``truncated=True`` if
    it was longer).

    The time bound is the timeout handed to the runner; the default
    :func:`~pebble_mcp.flow.subprocess_runner` returns whatever the stream
    emitted before the timeout fired.

    Both bounds are clamped defensively: ``seconds`` to ``[1, MAX_LOG_SECONDS]``
    (with non-finite values treated as the ceiling) so a huge/``inf`` value can
    never block the emulator forever, and ``max_bytes`` to ``>= 1`` so a
    zero/negative value can't turn the tail slice into "return everything".
    """
    _require_pebble()
    # Bound the time window: clamp to [1, MAX_LOG_SECONDS] and treat NaN/inf as
    # the ceiling so a non-finite value can never blow up int() or block forever.
    if not math.isfinite(seconds) or seconds > MAX_LOG_SECONDS:
        timeout = MAX_LOG_SECONDS
    else:
        timeout = max(1, int(round(seconds)))
    # Bound the byte window: a zero/negative max_bytes would otherwise make the
    # tail slice ``encoded[-max_bytes:]`` return the whole (or a wrong) buffer.
    if max_bytes < 1:
        max_bytes = 1
    rc, out = runner(["pebble", "logs", "--emulator", platform], None, timeout)

    matched = False
    if until_pattern is not None:
        cut: list[str] = []
        for line in out.splitlines(keepends=True):
            cut.append(line)
            if until_pattern in line:
                matched = True
                break
        out = "".join(cut)

    truncated = False
    encoded = out.encode("utf-8", errors="replace")
    if len(encoded) > max_bytes:
        out = encoded[-max_bytes:].decode("utf-8", errors="replace")
        truncated = True

    return LogsResult(text=out, returncode=rc, truncated=truncated, matched=matched)
