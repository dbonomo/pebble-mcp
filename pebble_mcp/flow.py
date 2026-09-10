"""Flow engine — the reusable core extracted from ``tools/gallery.py``.

A *flow* is a small line-oriented spec that drives the emery emulator through
the ``pebble`` CLI and captures screenshots. This module owns two concerns and
nothing else:

* **parsing** a flow spec into typed :class:`Step` dataclasses
  (:func:`parse_flow`), and
* **driving** the emulator to execute a parsed flow (:func:`run_flow`),
  including the kill+wipe-before-install behaviour and one-shot wedge recovery.

Presentation (contact sheets, ``index.html``, ``README.md``, argument parsing)
lives in the frontends — ``tools/gallery.py`` today, an MCP tool later. This is
the single engine both share, so there is no behaviour divergence.

Flow spec line format (one command per line, ``#`` starts a comment)::

    app <dir>            install (pebble install --emulator emery) this project dir;
                         following shots go under <out_dir>/<dir-basename>/
    pbw <path>           install a PREBUILT .pbw directly (same kill+wipe
                         semantics as app); following shots go under
                         <out_dir>/<pbw-basename-sans-.pbw>/. <path> is
                         resolved relative to repo_root when not absolute.
    wait <sec>           sleep
    shot <name>          pebble screenshot -> <out_dir>/<app>/NN-name.png (NN auto)
    press <back|up|select|down>     emu-button click
    longpress <btn> <ms>            emu-button click --duration <ms>
    tap                             emu-tap (accel tap)

Pacing is automatic: the driver settles briefly after every input
(:data:`BUTTON_SETTLE_SEC`) and after a successful install
(:data:`POST_INSTALL_SETTLE_SEC`), because the emery emulator desyncs on
back-to-back presses and a fresh install needs a moment to render. Explicit
``wait`` steps add to these rather than being required for correctness.

All subprocess calls to ``pebble`` go through a small injectable *runner*
(:data:`PebbleRunner`) so tests can stub the CLI entirely.
"""

from __future__ import annotations

import os
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass, field

# Substrings in command output that mean the emulator is wedged and needs a
# kill+wipe rather than a plain retry. Kept identical to the original tool.
WEDGE_MARKERS = (
    "not responding",
    "no emulator",
    "failed to connect",
    "connection refused",
    "timed out",
)

# Buttons the emery emulator accepts for emu-button.
VALID_BUTTONS = ("back", "up", "select", "down")

# Emulator pacing, baked in so correctness never depends on a flow author
# remembering to insert waits:
#   * The emery emulator desyncs / drops to the watchface when button presses
#     arrive back-to-back, so we settle briefly after every input.
#   * A freshly installed app needs a moment to render before it can be driven
#     or screenshotted. Explicit ``wait`` steps add to these, never replace them.
BUTTON_SETTLE_SEC = 0.3
POST_INSTALL_SETTLE_SEC = 3.0

# A ``shot`` name becomes a filename component (``NN-<name>.png``). Cap its
# length so the final filename stays well under the 255-byte filesystem limit.
MAX_SHOT_NAME = 100

# 8-byte PNG signature. ``pebble screenshot`` must write a real PNG; a
# zero-byte OR non-PNG file means a failed/wedged capture, not a valid shot.
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def _is_png(path: str) -> bool:
    """True if ``path`` begins with the 8-byte PNG signature."""
    try:
        with open(path, "rb") as f:
            return f.read(8) == PNG_MAGIC
    except OSError:
        return False


def _within(base: str, target: str) -> bool:
    """True if ``target`` resolves inside (or equal to) ``base`` (lexically)."""
    base_n = os.path.abspath(base)
    target_n = os.path.abspath(target)
    return target_n == base_n or target_n.startswith(base_n + os.sep)


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #
class FlowParseError(ValueError):
    """Raised when a flow spec contains a malformed or unknown command."""


class Wedged(Exception):
    """Raised internally when the emulator appears wedged mid-flow."""


# --------------------------------------------------------------------------- #
# Parsed step types
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class AppStep:
    """Install ``project_dir`` and route following shots under it."""

    project_dir: str


@dataclass(frozen=True)
class PbwStep:
    """Install a prebuilt ``.pbw`` at ``pbw_path`` and route following shots under it."""

    pbw_path: str


@dataclass(frozen=True)
class WaitStep:
    """Sleep for ``seconds``."""

    seconds: float


@dataclass(frozen=True)
class ShotStep:
    """Capture a screenshot named ``name`` (filename gets an auto NN- prefix)."""

    name: str


@dataclass(frozen=True)
class PressStep:
    """Click ``button`` once."""

    button: str


@dataclass(frozen=True)
class LongPressStep:
    """Hold ``button`` for ``duration_ms`` milliseconds."""

    button: str
    duration_ms: int


@dataclass(frozen=True)
class TapStep:
    """Fire an accelerometer tap."""


Step = AppStep | PbwStep | WaitStep | ShotStep | PressStep | LongPressStep | TapStep


@dataclass
class Flow:
    """A parsed flow: a name plus its ordered steps."""

    name: str
    steps: list[Step]


# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #
def parse_flow(text: str, *, name: str = "flow") -> Flow:
    """Parse flow-spec ``text`` into a :class:`Flow`.

    Raises :class:`FlowParseError` on unknown commands or malformed arguments,
    reporting the 1-based line number.
    """
    steps: list[Step] = []
    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        parts = line.split()
        cmd, args = parts[0], parts[1:]
        steps.append(_parse_step(cmd, args, lineno))
    return Flow(name=name, steps=steps)


def parse_flow_file(path: str) -> Flow:
    """Read and parse a flow file; the flow name is the basename sans extension."""
    with open(path) as f:
        text = f.read()
    name = os.path.splitext(os.path.basename(path))[0]
    return parse_flow(text, name=name)


def _parse_step(cmd: str, args: list[str], lineno: int) -> Step:
    if cmd == "app":
        _need(args, 1, cmd, lineno)
        return AppStep(project_dir=_project_dir(args[0], lineno))
    if cmd == "pbw":
        _need(args, 1, cmd, lineno)
        return PbwStep(pbw_path=_pbw_path(args[0], lineno))
    if cmd == "wait":
        _need(args, 1, cmd, lineno)
        return WaitStep(seconds=_to_float(args[0], cmd, lineno))
    if cmd == "shot":
        _need(args, 1, cmd, lineno)
        return ShotStep(name=_shot_name(args[0], lineno))
    if cmd == "press":
        _need(args, 1, cmd, lineno)
        return PressStep(button=_button(args[0], lineno))
    if cmd == "longpress":
        _need(args, 2, cmd, lineno)
        return LongPressStep(
            button=_button(args[0], lineno),
            duration_ms=_to_int(args[1], cmd, lineno),
        )
    if cmd == "tap":
        _need(args, 0, cmd, lineno)
        return TapStep()
    raise FlowParseError(f"line {lineno}: unknown command {cmd!r}")


def _shot_name(name: str, lineno: int) -> str:
    """Validate a ``shot`` name that becomes a single ``NN-<name>.png`` filename.

    A shot name is written verbatim into the output path, so it must be a plain
    filename component — never a path. Reject path separators, ``.``/``..``,
    null bytes / control characters, and over-long names. This is what makes an
    ``out_dir`` escape via a crafted ``shot`` line impossible (see the runtime
    containment guard in ``_Driver.run`` for defence in depth).
    """
    if not name:
        raise FlowParseError(f"line {lineno}: 'shot' name is empty")
    if len(name) > MAX_SHOT_NAME:
        raise FlowParseError(
            f"line {lineno}: 'shot' name is too long ({len(name)} > {MAX_SHOT_NAME} chars)"
        )
    if name in (".", ".."):
        raise FlowParseError(f"line {lineno}: 'shot' name {name!r} is not a valid filename")
    if "/" in name or "\\" in name or any(ord(c) < 0x20 or c == "\x00" for c in name):
        raise FlowParseError(
            f"line {lineno}: 'shot' name {name!r} may not contain path separators, "
            "null bytes, or control characters"
        )
    return name


def _project_dir(value: str, lineno: int) -> str:
    """Validate an ``app`` project dir: repo-relative, no traversal, no null byte.

    The value is joined onto both ``repo_root`` (install cwd) and ``out_dir``
    (shot destination), so an absolute path or a ``..`` hop would let shots and
    ``makedirs`` escape ``out_dir``. Ordinary sub-paths are still allowed.
    """
    if "\x00" in value:
        raise FlowParseError(f"line {lineno}: 'app' dir may not contain a null byte")
    if os.path.isabs(value):
        raise FlowParseError(
            f"line {lineno}: 'app' dir must be repo-relative, not absolute: {value!r}"
        )
    parts = value.replace("\\", "/").split("/")
    if ".." in parts:
        raise FlowParseError(
            f"line {lineno}: 'app' dir may not contain a '..' path-traversal hop: {value!r}"
        )
    return value


def _pbw_path(value: str, lineno: int) -> str:
    """Validate a ``pbw`` path. Prebuilt bundles may live anywhere on disk, so
    absolute and outside-repo paths are allowed *by design*; only null bytes
    (which would crash the install) are rejected. Shots for a ``pbw`` route
    under ``os.path.basename(path)``, which can never contain a separator, so no
    ``out_dir`` escape is possible regardless of the path.
    """
    if "\x00" in value:
        raise FlowParseError(f"line {lineno}: 'pbw' path may not contain a null byte")
    return value


def _need(args: list[str], n: int, cmd: str, lineno: int) -> None:
    if len(args) != n:
        raise FlowParseError(f"line {lineno}: {cmd!r} expects {n} argument(s), got {len(args)}")


def _button(value: str, lineno: int) -> str:
    if value not in VALID_BUTTONS:
        raise FlowParseError(
            f"line {lineno}: invalid button {value!r} (expected one of {VALID_BUTTONS})"
        )
    return value


def _to_float(value: str, cmd: str, lineno: int) -> float:
    try:
        return float(value)
    except ValueError as e:
        raise FlowParseError(f"line {lineno}: {cmd!r} expects a number, got {value!r}") from e


def _to_int(value: str, cmd: str, lineno: int) -> int:
    try:
        return int(value)
    except ValueError as e:
        raise FlowParseError(f"line {lineno}: {cmd!r} expects an integer, got {value!r}") from e


# --------------------------------------------------------------------------- #
# Running
# --------------------------------------------------------------------------- #
# A runner takes (cmd, cwd, timeout) and returns (returncode, combined_output).
# Isolating subprocess behind this makes the driver fully testable.
PebbleRunner = Callable[[list[str], "str | None", int], "tuple[int, str]"]


def _default_env() -> dict[str, str]:
    env = dict(os.environ)
    env["PATH"] = os.path.expanduser("~/.local/bin") + os.pathsep + env.get("PATH", "")
    return env


def subprocess_runner(
    cmd: list[str], cwd: str | None = None, timeout: int = 120
) -> tuple[int, str]:
    """Default :data:`PebbleRunner`: actually invoke the ``pebble`` CLI."""
    try:
        p = subprocess.run(
            cmd,
            cwd=cwd,
            env=_default_env(),
            timeout=timeout,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        return p.returncode, p.stdout or ""
    except subprocess.TimeoutExpired as e:
        return 124, (e.output or "") if isinstance(e.output, str) else "timeout"


def looks_wedged(out: str) -> bool:
    """True if command output contains a known emulator-wedge marker."""
    low = out.lower()
    return any(m in low for m in WEDGE_MARKERS)


@dataclass
class Shot:
    """One captured screenshot."""

    app: str
    index: int
    name: str
    filename: str
    path: str
    duration_s: float


@dataclass
class FlowResult:
    """The outcome of running a flow."""

    name: str
    shots: list[Shot] = field(default_factory=list)
    retries: int = 0
    wedge_recoveries: int = 0
    restarted: bool = False


class _Driver:
    """Executes a parsed flow against an injectable pebble runner."""

    def __init__(
        self,
        out_dir: str,
        repo_root: str,
        runner: PebbleRunner,
        sleep: Callable[[float], None],
        log: Callable[[str], None],
    ) -> None:
        self.out_dir = out_dir
        self.repo_root = repo_root
        self.runner = runner
        self.sleep = sleep
        self.log = log
        self.retries = 0

    def _run(self, cmd: list[str], cwd: str | None = None, timeout: int = 120) -> tuple[int, str]:
        self.log("    $ " + " ".join(cmd))
        return self.runner(cmd, cwd, timeout)

    def install_app(self, project_dir: str) -> None:
        cwd = os.path.join(self.repo_root, project_dir)
        pbw = os.path.join("build", project_dir + ".pbw")
        # A clean kill+wipe before install is what makes a watchapp actually
        # LAUNCH (a persisted active watchface otherwise stays foreground) and
        # avoids the "not responding" wedge from installing over a running emu.
        self._run(["pebble", "kill"], timeout=60)
        self._run(["pebble", "wipe"], timeout=60)
        self.sleep(2)
        out = ""
        for attempt in range(2):
            rc, out = self._run(
                ["pebble", "install", "--emulator", "emery", pbw], cwd=cwd, timeout=180
            )
            if rc == 0:
                self.sleep(POST_INSTALL_SETTLE_SEC)
                return
            if looks_wedged(out) or attempt == 1:
                raise Wedged("install failed: " + out[-300:])
            self.log("    ! install failed, retrying once")
            self.sleep(2)

    def install_pbw(self, pbw_path: str) -> None:
        # Resolve a project-relative path against repo_root; absolute paths pass
        # through. Same kill+wipe-first + one-retry semantics as install_app,
        # but installs a prebuilt bundle directly (no build step, no project cwd).
        if not os.path.isabs(pbw_path):
            pbw_path = os.path.join(self.repo_root, pbw_path)
        self._run(["pebble", "kill"], timeout=60)
        self._run(["pebble", "wipe"], timeout=60)
        self.sleep(2)
        out = ""
        for attempt in range(2):
            rc, out = self._run(["pebble", "install", "--emulator", "emery", pbw_path], timeout=180)
            if rc == 0:
                self.sleep(POST_INSTALL_SETTLE_SEC)
                return
            if looks_wedged(out) or attempt == 1:
                raise Wedged("install failed: " + out[-300:])
            self.log("    ! install failed, retrying once")
            self.sleep(2)

    def screenshot(self, dest: str) -> None:
        out = ""
        for attempt in range(2):
            rc, out = self._run(["pebble", "screenshot", "--no-open", dest], timeout=90)
            if rc == 0 and os.path.exists(dest) and os.path.getsize(dest) > 0:
                if not _is_png(dest):
                    # A non-PNG (or truncated) file means the capture produced
                    # garbage — treat like a wedge so it gets one recovery pass
                    # rather than surfacing a corrupt "shot".
                    raise Wedged(f"screenshot wrote a non-PNG file ({os.path.getsize(dest)} bytes)")
                return
            if looks_wedged(out):
                raise Wedged("screenshot wedge: " + out[-200:])
            if attempt == 0:
                self.retries += 1
                self.log("    ! screenshot failed, retrying once")
                self.sleep(2)
        raise Wedged("screenshot failed twice: " + out[-200:])

    def button(self, action_args: list[str]) -> None:
        out = ""
        for attempt in range(2):
            rc, out = self._run(["pebble", "emu-button"] + action_args, timeout=60)
            if rc == 0:
                # Pace before the next step — the emery emulator desyncs on
                # back-to-back presses regardless of what the flow does next.
                self.sleep(BUTTON_SETTLE_SEC)
                return
            if looks_wedged(out):
                raise Wedged("button wedge: " + out[-200:])
            if attempt == 0:
                self.retries += 1
                self.log("    ! button failed, retrying once")
                self.sleep(1)
        raise Wedged("button failed twice: " + out[-200:])

    def tap(self) -> None:
        rc, out = self._run(["pebble", "emu-tap"], timeout=60)
        if rc != 0 and looks_wedged(out):
            raise Wedged("tap wedge")
        if rc == 0:
            self.sleep(BUTTON_SETTLE_SEC)

    def unwedge(self) -> None:
        self.log("  >> emulator wedged: pebble kill && pebble wipe")
        self._run(["pebble", "kill"], timeout=60)
        self._run(["pebble", "wipe"], timeout=60)
        self.sleep(2)

    def run(self, flow: Flow, restarted: bool = False, recoveries: int = 0) -> FlowResult:
        self.log(f"== flow: {flow.name} ==")
        app: str | None = None
        outdir: str | None = None
        counter = 0
        shots: list[Shot] = []
        try:
            for step in flow.steps:
                if isinstance(step, AppStep):
                    app = step.project_dir
                    outdir = os.path.join(self.out_dir, app)
                    if not _within(self.out_dir, outdir):
                        raise FlowParseError(f"app {app!r} would escape the output directory")
                    os.makedirs(outdir, exist_ok=True)
                    self.log(f"  -- install {app}")
                    self.install_app(app)
                elif isinstance(step, PbwStep):
                    app = os.path.splitext(os.path.basename(step.pbw_path))[0]
                    outdir = os.path.join(self.out_dir, app)
                    os.makedirs(outdir, exist_ok=True)
                    self.log(f"  -- install pbw {step.pbw_path}")
                    self.install_pbw(step.pbw_path)
                elif isinstance(step, WaitStep):
                    self.sleep(step.seconds)
                elif isinstance(step, ShotStep):
                    if outdir is None or app is None:
                        raise FlowParseError("shot before any app step")
                    counter += 1
                    fname = f"{counter:02d}-{step.name}.png"
                    dest = os.path.join(outdir, fname)
                    if not _within(outdir, dest):
                        raise FlowParseError(
                            f"shot {step.name!r} would escape the output directory"
                        )
                    self.log(f"  -- shot {fname}")
                    t0 = time.monotonic()
                    self.screenshot(dest)
                    shots.append(Shot(app, counter, step.name, fname, dest, time.monotonic() - t0))
                elif isinstance(step, PressStep):
                    self.button(["click", step.button])
                elif isinstance(step, LongPressStep):
                    self.button(["click", step.button, "--duration", str(step.duration_ms)])
                elif isinstance(step, TapStep):
                    self.tap()
            return FlowResult(flow.name, shots, self.retries, recoveries, restarted)
        except Wedged as e:
            self.log(f"  !! {e}")
            if restarted:
                self.log("  !! already restarted once; giving up on this flow")
                raise
            self.unwedge()
            # Restart the whole flow once so shot numbering restarts clean.
            return self.run(flow, restarted=True, recoveries=recoveries + 1)


def run_flow(
    flow: Flow,
    out_dir: str,
    *,
    repo_root: str,
    runner: PebbleRunner = subprocess_runner,
    sleep: Callable[[float], None] = time.sleep,
    log: Callable[[str], None] | None = None,
) -> FlowResult:
    """Execute ``flow``, writing shots under ``out_dir/<app>/NN-name.png``.

    ``repo_root`` resolves each ``app`` step's project directory (the install
    ``cwd``). On an emulator wedge the driver runs ``pebble kill && pebble
    wipe`` and restarts the flow exactly once; a second wedge re-raises
    :class:`Wedged`.

    Pass ``runner`` to stub the ``pebble`` CLI (tests), ``sleep`` to skip real
    waits, and ``log`` to receive progress lines (defaults to silent).
    """
    driver = _Driver(out_dir, repo_root, runner, sleep, log or (lambda _msg: None))
    return driver.run(flow)
