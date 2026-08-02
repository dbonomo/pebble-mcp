"""SVG -> PDC (Pebble Draw Command) conversion, with constraint validation
that runs *before* conversion so a bad SVG returns structured violations
instead of a broken or silently-wrong file.

## Provenance

The binary format and coordinate-transform logic here are adapted from the
official Pebble example tool ``svg2pdc.py`` (Copyright (c) 2015 Pebble
Technology, from ``pebble-examples/cards-example/tools/``, referenced by
ECOSYSTEM.md "Icons & vector graphics (PDC)"). That script is Python 2 and
depends on two packages we don't want to add (``svg.path`` for curve
flattening, ``pebble_image_routines`` for palette snapping) -- see "What's
different from svg2pdc.py" below. The struct layouts (file header, command
header, point encoding) and the pixel-grid coordinate transform
(``_to_pebble_point``) are carried over faithfully; comments below cite the
matching logic in the original.

Cross-checked against the official guide, "Converting SVG to PDC"
(https://developer.repebble.com/guides/app-resources/converting-svg-to-pdc/,
fetched 2026-07-31): "The svg2pdc tool currently supports SVG files that use
only the following elements: g, layer, path, rect, polyline, polygon, line,
circle" -- reproduced in ``_SUPPORTED_ELEMENTS`` below -- and the worked
example "Invalid point: (9.4, 44.5). Closest supported coordinate: (9.5,
44.5)", which is the same half-pixel-grid nearest-point logic implemented in
``_nearest_valid_point``.

## What's different from svg2pdc.py (and why, to keep zero new deps)

* **No ``svg.path`` dependency.** Path ``d`` attributes are parsed here with
  a small hand-rolled tokenizer supporting the straight-line commands
  ``M/m L/l H/h V/v Z/z`` (everything a "flatten groups, ungroup, plain SVG"
  export from Inkscape/Illustrator produces per the official guide's own
  Inkscape/Illustrator walkthrough). Curve commands (``C S Q T A`` and their
  lowercase forms) are reported as a **violation** rather than silently
  approximated -- svg2pdc.py's own approach (taking each curve segment's
  ``.start`` point only) quietly distorts the shape, which is exactly the
  "return structured violations instead of a broken file" failure mode this
  tool is built to avoid.
* **No ``pebble_image_routines`` dependency.** Fill/stroke colors are
  snapped to the 64-color palette using this package's own
  ``pebble_mcp.palette.nearest()`` (already golden-tested against the
  vendored palette files) instead of vendoring the SDK's C-derived palette
  routines.
* Only the non-sequence, non-"precise" image path is implemented (a single
  static ``PDCI``, standard 1px-grid coordinates) -- the common icon use case
  (SVGs that follow the PDC style guide convert cleanly) doesn't need PDC
  sequences (``PDCS``) or 8x sub-pixel precision, and skipping them keeps this
  module small.
* Group ``transform`` support is limited to ``translate(x, y)`` (also the
  only transform svg2pdc.py itself understands); other transforms
  (``scale``/``rotate``/``matrix``/...) are reported as a violation instead
  of being silently ignored.
"""

from __future__ import annotations

import math
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from struct import pack

from pebble_mcp import palette

XMLNS = "{http://www.w3.org/2000/svg}"

# Elements the official svg2pdc.py pipeline (and thus this adaptation)
# understands. "svg" and "defs" are containers we walk through/ignore.
_SUPPORTED_ELEMENTS = {"g", "layer", "path", "rect", "polyline", "polygon", "line", "circle"}
_CONTAINER_ELEMENTS = {"svg", "defs"}
# Elements that are an unambiguous PDC violation even though they're valid SVG.
_EXPLICITLY_UNSUPPORTED = {
    "text": "text is not supported by PDC -- convert text to paths/outlines first",
    "tspan": "text is not supported by PDC -- convert text to paths/outlines first",
    "image": "embedded raster images are not supported by PDC",
    "ellipse": "ellipse is not supported (PDC circle needs equal rx/ry) -- convert to circle/path",
    "use": "<use> element references are not supported -- inline the referenced shape",
    "linearGradient": "gradients are not supported by PDC -- use a flat fill color",
    "radialGradient": "gradients are not supported by PDC -- use a flat fill color",
    "mask": "masks are not supported by PDC",
    "clipPath": "clip paths are not supported by PDC",
    "filter": "filter effects are not supported by PDC",
}

_CURVE_COMMANDS = set("CcSsQqTtAa")
_PATH_TOKEN_RE = re.compile(r"([MmLlHhVvZz])|(-?\d*\.?\d+(?:[eE][-+]?\d+)?)")
_URL_REF_RE = re.compile(r"url\(\s*#")
_TRANSLATE_RE = re.compile(r"translate\(\s*(-?[\d.]+)[,\s]+(-?[\d.]+)\s*\)")

DRAW_COMMAND_VERSION = 1
_TYPE_PATH = 1
_TYPE_CIRCLE = 2

# Serialized coordinate ranges (see ``_serialize`` and the ``serialize`` methods
# below): point coordinates and the file-header width/height are signed int16;
# a circle radius is unsigned int16. A coordinate that lands outside these can't
# be encoded, so it is reported as a violation instead of crashing ``struct.pack``
# with a raw ``struct.error`` / ``OverflowError`` / ``ValueError`` (NaN).
_COORD_MIN, _COORD_MAX = -32768, 32767
_RADIUS_MAX = 65535

# Cap on SVG group nesting we will recurse through. Watch-icon SVGs are shallow
# (a handful of <g> layers); anything deeper is almost certainly hostile or
# machine-generated and is reported as a violation rather than being allowed to
# overflow the Python call stack with an uncaught RecursionError.
_MAX_NESTING_DEPTH = 100


def _add_unique(violations: list[str], message: str) -> None:
    """Append ``message`` unless an identical one is already present.

    Keeps the violation list bounded when a hostile document repeats the same
    problem thousands of times (deep nesting, many out-of-range points)."""
    if message not in violations:
        violations.append(message)


def _validated_point(
    p: tuple[float, float],
    translate: tuple[float, float],
    what: str,
    warnings: list[str],
    violations: list[str],
) -> tuple[int, int] | None:
    """Transform ``p`` to a Pebble integer point, or record a violation and
    return ``None`` if it is non-finite or outside the encodable int16 range."""
    if not (math.isfinite(p[0]) and math.isfinite(p[1])):
        _add_unique(violations, f"{what} has a non-finite coordinate ({p[0]}, {p[1]})")
        return None
    px, py, _exact = _to_pebble_point(p, translate)
    if not (_COORD_MIN <= px <= _COORD_MAX and _COORD_MIN <= py <= _COORD_MAX):
        _add_unique(
            violations,
            f"{what} ({px}, {py}) is outside the PDC coordinate range "
            f"[{_COORD_MIN}, {_COORD_MAX}] -- rescale the artwork to fit the watch screen",
        )
        return None
    _warn_if_odd(px, py, what, warnings)
    return (px, py)


# ---------------------------------------------------------------------------
# Coordinate transform (see module docstring: ported from svg2pdc.py)
# ---------------------------------------------------------------------------
def _nearest_valid_point(p: tuple[float, float]) -> tuple[float, float]:
    """Nearest point on the half-pixel grid svg2pdc.py treats as "exact"."""
    return (round(p[0] * 2.0) / 2.0, round(p[1] * 2.0) / 2.0)


def _to_pebble_point(
    p: tuple[float, float], translate: tuple[float, float]
) -> tuple[int, int, bool]:
    """Translate + shift-by-(-0.5,-0.5) + round, matching
    ``convert_to_pebble_coordinates`` in svg2pdc.py. Returns (x, y, was_exact).
    """
    tx, ty = p[0] + translate[0], p[1] + translate[1]
    exact = (tx, ty) == _nearest_valid_point((tx, ty))
    return (round(tx - 0.5), round(ty - 0.5), exact)


# ---------------------------------------------------------------------------
# Draw commands
# ---------------------------------------------------------------------------
@dataclass
class _PathCommand:
    points: list[tuple[int, int]]
    open: bool
    stroke_color: int = 0
    stroke_width: int = 0
    fill_color: int = 0

    def serialize(self) -> bytes:
        s = pack("B", _TYPE_PATH)
        s += pack("<BBBB", 0, self.stroke_color, self.stroke_width, self.fill_color)
        s += pack("<BB", int(self.open), 0)
        s += pack("<H", len(self.points))
        for x, y in self.points:
            s += pack("<hh", x, y)
        return s


@dataclass
class _CircleCommand:
    center: tuple[int, int]
    radius: int
    stroke_color: int = 0
    stroke_width: int = 0
    fill_color: int = 0

    def serialize(self) -> bytes:
        s = pack("B", _TYPE_CIRCLE)
        s += pack("<BBBB", 0, self.stroke_color, self.stroke_width, self.fill_color)
        s += pack("<H", self.radius)
        s += pack("<H", 1)
        s += pack("<hh", *self.center)
        return s


@dataclass
class PdcResult:
    """Result of :func:`pdc_convert`.

    ``valid`` is False whenever ``violations`` is non-empty -- in that case
    ``pdc_bytes`` is always ``None``. ``warnings`` are non-fatal (e.g. odd
    coordinates) and may be present alongside a successful conversion.
    """

    valid: bool
    pdc_bytes: bytes | None
    width: int | None
    height: int | None
    num_commands: int | None
    violations: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def size_bytes(self) -> int | None:
        return len(self.pdc_bytes) if self.pdc_bytes is not None else None


# ---------------------------------------------------------------------------
# Color parsing
# ---------------------------------------------------------------------------
def _parse_hex_color(value: str | None) -> tuple[int, ...] | None:
    """Parse a ``#rrggbb``/``#rgb`` color to a palette-snapped ``argb8`` byte,
    or ``None`` for "no such paint" (``fill="none"``/unset/non-hex)."""
    if not value or value in ("none", "transparent"):
        return None
    if not value.startswith("#"):
        # Named CSS colors / url() refs are handled by the caller (url refs
        # are flagged as violations before we get here); anything else we
        # can't resolve is treated as "no paint" rather than guessed at.
        return None
    match = palette.nearest(value)
    entry = palette.get(match.hex)
    assert entry is not None
    return entry.argb8


def _tag_name(el: ET.Element) -> str:
    tag = el.tag
    if tag.startswith(XMLNS):
        return tag[len(XMLNS) :]
    return tag


def _style_or_attr(el: ET.Element, style_dict: dict[str, str], name: str) -> str | None:
    if name in style_dict:
        return style_dict[name]
    return el.get(name)


def _paint_attrs(el: ET.Element, inherited: dict[str, str]) -> tuple[int, int, int]:
    """Returns (fill_argb8_byte, stroke_argb8_byte, stroke_width)."""
    style_raw = el.get("style") or ""
    style = dict(item.split(":", 1) for item in style_raw.split(";") if ":" in item)
    fill_raw = _style_or_attr(el, style, "fill")
    stroke_raw = _style_or_attr(el, style, "stroke")
    stroke_width_raw = _style_or_attr(el, style, "stroke-width")

    fill_raw = fill_raw if fill_raw is not None else inherited.get("fill")
    stroke_raw = stroke_raw if stroke_raw is not None else inherited.get("stroke")

    fill_byte = _parse_hex_color(fill_raw) or 0
    stroke_byte = _parse_hex_color(stroke_raw) or 0

    try:
        if stroke_width_raw:
            stroke_width = int(float(stroke_width_raw))
        else:
            stroke_width = 1 if stroke_byte else 0
    except ValueError:
        stroke_width = 1 if stroke_byte else 0

    if stroke_width == 0:
        stroke_byte = 0
    if stroke_byte == 0:
        stroke_width = 0

    return fill_byte, stroke_byte, stroke_width


# ---------------------------------------------------------------------------
# Path `d` tokenizer -- straight-line commands only (see module docstring).
# ---------------------------------------------------------------------------
def _tokenize_path(d: str) -> list[str]:
    tokens: list[str] = []
    for cmd_m, num_m in _PATH_TOKEN_RE.findall(d):
        tokens.append(cmd_m if cmd_m else num_m)
    return tokens


def _parse_path_d(d: str) -> tuple[list[tuple[float, float]], bool] | None:
    """Returns (points, closed) for a straight-line-only path, or None if the
    path uses curve commands (caller records that as a violation)."""
    if any(c in _CURVE_COMMANDS for c in d):
        return None

    tokens = _tokenize_path(d)
    points: list[tuple[float, float]] = []
    i = 0
    cmd = None
    cur = (0.0, 0.0)
    start = (0.0, 0.0)
    closed = False
    while i < len(tokens):
        tok = tokens[i]
        if tok.isalpha():
            cmd = tok
            i += 1
            continue
        if cmd is None:
            return None
        if cmd in ("M", "m"):
            x, y = float(tokens[i]), float(tokens[i + 1])
            i += 2
            cur = (x, y) if cmd == "M" else (cur[0] + x, cur[1] + y)
            points.append(cur)
            start = cur
            cmd = "L" if cmd == "M" else "l"  # subsequent pairs are implicit lineto
        elif cmd in ("L", "l"):
            x, y = float(tokens[i]), float(tokens[i + 1])
            i += 2
            cur = (x, y) if cmd == "L" else (cur[0] + x, cur[1] + y)
            points.append(cur)
        elif cmd in ("H", "h"):
            x = float(tokens[i])
            i += 1
            cur = (x, cur[1]) if cmd == "H" else (cur[0] + x, cur[1])
            points.append(cur)
        elif cmd in ("V", "v"):
            y = float(tokens[i])
            i += 1
            cur = (cur[0], y) if cmd == "V" else (cur[0], cur[1] + y)
            points.append(cur)
        elif cmd in ("Z", "z"):
            closed = True
            cur = start
            i += 1
        else:  # pragma: no cover - guarded by _CURVE_COMMANDS check above
            return None

    if closed and points and points[0] == points[-1]:
        points = points[:-1]
    return points, closed


# ---------------------------------------------------------------------------
# Violation scan (runs before any conversion is attempted)
# ---------------------------------------------------------------------------
def validate_svg(svg_text: str) -> list[str]:
    """Scan an SVG document for PDC-incompatible content, without converting
    it. Returns a list of human-readable violation strings (empty = clean).

    Checks, per the official svg2pdc element list and ECOSYSTEM.md: only
    ``g``/``layer``/``path``/``rect``/``polyline``/``polygon``/``line``/
    ``circle`` elements; no gradients, masks, clip paths, filters, or text;
    no ``url(#...)`` paint references; no path curve commands; no non-
    ``translate`` transforms.
    """
    try:
        root = ET.fromstring(svg_text)
    except ET.ParseError as exc:
        return [f"malformed SVG/XML: {exc}"]

    violations: list[str] = []
    _scan_element(root, violations, is_root=True)
    return violations


def _scan_element(
    el: ET.Element, violations: list[str], *, is_root: bool = False, depth: int = 0
) -> None:
    if depth > _MAX_NESTING_DEPTH:
        _add_unique(
            violations,
            f"SVG nesting is too deep (>{_MAX_NESTING_DEPTH} levels) -- ungroup/flatten "
            "the nested <g> elements before converting",
        )
        return
    tag = _tag_name(el)
    if not is_root:
        if tag in _EXPLICITLY_UNSUPPORTED:
            violations.append(f"<{tag}>: {_EXPLICITLY_UNSUPPORTED[tag]}")
        elif tag not in _SUPPORTED_ELEMENTS and tag not in _CONTAINER_ELEMENTS:
            violations.append(f"<{tag}>: unsupported element (svg2pdc supports g/layer/path/rect/"
                               "polyline/polygon/line/circle only)")

        transform = el.get("transform")
        if transform and not _TRANSLATE_RE.fullmatch(transform.strip()):
            violations.append(
                f"<{tag} transform=\"{transform}\">: only translate(x, y) transforms are supported"
            )

        for attr in ("fill", "stroke"):
            val = el.get(attr)
            if val and _URL_REF_RE.search(val):
                violations.append(
                    f'<{tag} {attr}="{val}">: url() paint references (gradients/patterns) '
                    "are not supported -- use a flat fill color"
                )

        if tag == "path":
            d = el.get("d")
            if d and any(c in _CURVE_COMMANDS for c in d):
                curve_chars = sorted({c for c in d if c in _CURVE_COMMANDS})
                violations.append(
                    f"<path d=\"...\">: curve command(s) {curve_chars} not supported -- "
                    "flatten to line segments (M/L/H/V/Z only) before converting"
                )

    if tag in _SUPPORTED_ELEMENTS or tag in _CONTAINER_ELEMENTS or is_root:
        for child in el:
            _scan_element(child, violations, depth=depth + 1)


# ---------------------------------------------------------------------------
# Conversion
# ---------------------------------------------------------------------------
def _get_size(root: ET.Element) -> tuple[float, float, float, float]:
    """Returns (min_x, min_y, width, height) from viewBox or width/height.

    Never raises on malformed input: a non-numeric viewBox token falls through
    to the width/height attributes, and unparseable width/height yield 0.
    """
    vb = root.get("viewBox")
    if vb:
        try:
            parts = [float(v) for v in vb.replace(",", " ").split()]
        except ValueError:
            parts = []
        if len(parts) == 4:
            return tuple(parts)  # type: ignore[return-value]
    try:
        w = float(re.sub(r"[a-zA-Z%]", "", root.get("width", "0")))
        h = float(re.sub(r"[a-zA-Z%]", "", root.get("height", "0")))
    except ValueError:
        w = h = 0.0
    return (0.0, 0.0, w, h)


def _dimension_violations(min_x: float, min_y: float, w: float, h: float) -> list[str]:
    """Reject non-finite or out-of-header-range canvas dimensions before we try
    to pack them into the signed-int16 file header."""
    issues: list[str] = []
    for label, val in (("viewBox min-x", min_x), ("viewBox min-y", min_y),
                       ("width", w), ("height", h)):
        if not math.isfinite(val):
            issues.append(f"{label} is not a finite number ({val})")
    for label, val in (("width", w), ("height", h)):
        if math.isfinite(val) and not (_COORD_MIN <= round(val) <= _COORD_MAX):
            issues.append(
                f"{label} {val} is outside the PDC canvas range [{_COORD_MIN}, {_COORD_MAX}]"
            )
    return issues


def _build_commands(
    root: ET.Element,
    translate: tuple[float, float],
    warnings: list[str],
    violations: list[str],
    inherited_paint: dict[str, str] | None = None,
    depth: int = 0,
) -> list[_PathCommand | _CircleCommand]:
    inherited_paint = inherited_paint or {}
    commands: list[_PathCommand | _CircleCommand] = []
    if depth > _MAX_NESTING_DEPTH:  # defensive; validate_svg already flags this
        return commands

    for el in root:
        tag = _tag_name(el)
        if tag in ("g", "layer"):
            child_translate = translate
            t = el.get("transform")
            m = _TRANSLATE_RE.fullmatch(t.strip()) if t else None
            if m:
                child_translate = (
                    translate[0] + float(m.group(1)),
                    translate[1] + float(m.group(2)),
                )
            child_paint = dict(inherited_paint)
            for attr in ("fill", "stroke", "stroke-width"):
                if el.get(attr) is not None:
                    child_paint[attr] = el.get(attr)
            commands.extend(
                _build_commands(el, child_translate, warnings, violations, child_paint, depth + 1)
            )
            continue
        if tag not in _SUPPORTED_ELEMENTS:
            continue

        fill_byte, stroke_byte, stroke_width = _paint_attrs(el, inherited_paint)
        if fill_byte == 0 and stroke_byte == 0:
            continue  # invisible shape (no fill, no stroke) -- nothing to draw
        paint = (stroke_byte, stroke_width, fill_byte)

        if tag == "circle":
            cx, cy = _num(el.get("cx", 0)), _num(el.get("cy", 0))
            r = _num(el.get("r") or el.get("z") or 0)
            center = _validated_point((cx, cy), translate, "circle center", warnings, violations)
            if center is None:
                continue
            if not math.isfinite(r) or not (0 <= round(r) <= _RADIUS_MAX):
                _add_unique(
                    violations,
                    f"circle radius {r} is outside the PDC range [0, {_RADIUS_MAX}]",
                )
                continue
            commands.append(_CircleCommand(center, round(r), *paint))
        elif tag == "rect":
            x, y = _num(el.get("x", 0)), _num(el.get("y", 0))
            w, h = _num(el.get("width", 0)), _num(el.get("height", 0))
            pts = [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]
            _append_path(commands, pts, False, translate, warnings, violations, paint)
        elif tag == "line":
            pts = [
                (_num(el.get("x1", 0)), _num(el.get("y1", 0))),
                (_num(el.get("x2", 0)), _num(el.get("y2", 0))),
            ]
            _append_path(commands, pts, True, translate, warnings, violations, paint)
        elif tag in ("polyline", "polygon"):
            pts = _parse_points_attr(el.get("points", ""))
            if not pts:
                continue
            _append_path(commands, pts, tag == "polyline", translate, warnings, violations, paint)
        elif tag == "path":
            d = el.get("d")
            if not d:
                continue
            parsed = _parse_path_d(d)
            if parsed is None:
                continue  # curve path; already reported by validate_svg()
            pts, closed = parsed
            if not pts:
                continue
            _append_path(commands, pts, not closed, translate, warnings, violations, paint)

    return commands


def _num(value) -> float:
    """Coerce an SVG numeric attribute to float; non-numeric -> 0.0 (never raises).

    Recognizes the literal float tokens ``nan``/``inf`` so that hostile
    coordinates are carried through as non-finite values and caught by the
    range/finiteness checks, rather than silently becoming 0."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _parse_points_attr(raw: str) -> list[tuple[float, float]]:
    # points="x1,y1 x2,y2 ..." -- split on whitespace, then on comma per pair.
    pts: list[tuple[float, float]] = []
    for chunk in raw.split():
        if "," in chunk:
            xs, ys = chunk.split(",", 1)
            pts.append((float(xs), float(ys)))
    return pts


def _append_path(
    commands: list,
    points: list[tuple[float, float]],
    open_: bool,
    translate: tuple[float, float],
    warnings: list[str],
    violations: list[str],
    paint: tuple[int, int, int],
) -> None:
    """Build a path command from ``points`` and append it, or record a violation
    and drop the shape if any point is non-finite / out of range.

    ``paint`` is ``(stroke_byte, stroke_width, fill_byte)``."""
    pebble_points = []
    for p in points:
        pt = _validated_point(p, translate, "path point", warnings, violations)
        if pt is None:
            return  # bad coordinate already recorded; drop this shape
        pebble_points.append(pt)
    stroke_byte, stroke_width, fill_byte = paint
    commands.append(_PathCommand(pebble_points, open_, stroke_byte, stroke_width, fill_byte))


_ODD_WARNING_CAP = 8


def _warn_if_odd(x: int, y: int, what: str, warnings: list[str]) -> None:
    if (x % 2 != 0 or y % 2 != 0) and len(warnings) < _ODD_WARNING_CAP:
        warnings.append(
            f"{what} ({x}, {y}) has an odd coordinate -- PDC recommends even-integer "
            "coordinates for crisp rendering; consider nudging this point to an even grid position"
        )


def _serialize(width: int, height: int, commands: list) -> bytes:
    body = pack("<BBhh", DRAW_COMMAND_VERSION, 0, width, height)
    body += pack("<H", len(commands))
    for c in commands:
        body += c.serialize()
    return b"PDCI" + pack("<I", len(body)) + body


def pdc_convert(svg_text: str) -> PdcResult:
    """Convert an SVG document to a PDC image, validating PDC constraints
    first. Returns a :class:`PdcResult`; check ``.valid`` before touching
    ``.pdc_bytes`` (it is ``None`` whenever there are violations).
    """
    violations = validate_svg(svg_text)
    if violations:
        return PdcResult(
            valid=False, pdc_bytes=None, width=None, height=None, num_commands=None,
            violations=violations, warnings=[],
        )

    root = ET.fromstring(svg_text)
    min_x, min_y, w, h = _get_size(root)

    dim_violations = _dimension_violations(min_x, min_y, w, h)
    if dim_violations:
        return PdcResult(
            valid=False, pdc_bytes=None, width=None, height=None, num_commands=None,
            violations=dim_violations, warnings=[],
        )

    translate = (-min_x, -min_y)
    warnings: list[str] = []
    coord_violations: list[str] = []
    commands = _build_commands(root, translate, warnings, coord_violations)

    if coord_violations:
        # A coordinate couldn't be encoded -- report it rather than emit a
        # truncated/wrong PDC. Consistent with the "structured violations, never
        # a broken file" contract that governs the pre-conversion scan.
        return PdcResult(
            valid=False, pdc_bytes=None, width=None, height=None, num_commands=None,
            violations=coord_violations, warnings=warnings,
        )

    width, height = round(w), round(h)
    pdc_bytes = _serialize(width, height, commands)

    return PdcResult(
        valid=True,
        pdc_bytes=pdc_bytes,
        width=width,
        height=height,
        num_commands=len(commands),
        violations=[],
        warnings=warnings,
    )
