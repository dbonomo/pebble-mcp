"""Tests for pebble_mcp.pdc."""

from __future__ import annotations

import struct

import pytest

from pebble_mcp.pdc import pdc_convert, validate_svg

SVG_NS = 'xmlns="http://www.w3.org/2000/svg"'


def _svg(body: str, *, width: int = 10, height: int = 10, viewbox: bool = True) -> str:
    vb = f'viewBox="0 0 {width} {height}"' if viewbox else ""
    return f'<svg {SVG_NS} width="{width}" height="{height}" {vb}>{body}</svg>'


# ---------------------------------------------------------------------------
# Happy path: a tiny valid SVG converts to plausible PDC bytes.
# ---------------------------------------------------------------------------


def test_valid_rect_svg_converts_to_pdc():
    svg = _svg('<rect x="1" y="1" width="8" height="8" fill="#FFFFFF"/>')
    result = pdc_convert(svg)

    assert result.valid is True
    assert result.violations == []
    assert result.pdc_bytes is not None

    # Magic word + size sanity, per the PDC file header (module docstring /
    # svg2pdc.py: 'PDCI' magic, little-endian uint32 size of what follows).
    assert result.pdc_bytes[:4] == b"PDCI"
    (declared_size,) = struct.unpack_from("<I", result.pdc_bytes, 4)
    assert declared_size == len(result.pdc_bytes) - 8
    assert result.size_bytes == len(result.pdc_bytes)

    assert result.width == 10
    assert result.height == 10
    assert result.num_commands == 1


def test_valid_svg_with_circle_and_polygon_multiple_commands():
    svg = _svg(
        '<circle cx="4" cy="4" r="2" fill="#00AAFF"/>'
        '<polygon points="0,0 8,0 4,8" fill="#00FF00"/>'
    )
    result = pdc_convert(svg)
    assert result.valid is True
    assert result.num_commands == 2
    assert result.pdc_bytes[:4] == b"PDCI"


def test_shape_with_no_fill_or_stroke_is_dropped_not_crashed():
    # No fill/stroke at all -- svg2pdc.py treats this as invisible and skips
    # it; we should do the same (not raise, not fabricate a color).
    svg = _svg('<rect x="0" y="0" width="4" height="4"/>')
    result = pdc_convert(svg)
    assert result.valid is True
    assert result.num_commands == 0


# ---------------------------------------------------------------------------
# Violation classes -- each must be detected before conversion is attempted.
# ---------------------------------------------------------------------------


def test_unsupported_element_ellipse_is_a_violation():
    svg = _svg('<ellipse cx="1" cy="1" rx="1" ry="2" fill="#000000"/>')
    violations = validate_svg(svg)
    assert any("ellipse" in v for v in violations)

    result = pdc_convert(svg)
    assert result.valid is False
    assert result.pdc_bytes is None
    assert result.violations == violations


def test_unsupported_element_generic():
    svg = _svg('<image href="x.png" width="4" height="4"/>')
    violations = validate_svg(svg)
    assert any("image" in v for v in violations)


def test_gradient_fill_is_a_violation():
    svg = _svg(
        '<defs><linearGradient id="g"/></defs>'
        '<rect x="0" y="0" width="4" height="4" fill="url(#g)"/>'
    )
    violations = validate_svg(svg)
    assert any("gradient" in v.lower() for v in violations)
    assert any("url(" in v for v in violations)


def test_mask_is_a_violation():
    svg = _svg('<mask id="m"/><rect x="0" y="0" width="4" height="4" fill="#000" mask="url(#m)"/>')
    violations = validate_svg(svg)
    assert any("mask" in v.lower() for v in violations)


def test_clip_path_is_a_violation():
    svg = _svg(
        '<clipPath id="c"/><rect x="0" y="0" width="4" height="4" fill="#000" clip-path="url(#c)"/>'
    )
    violations = validate_svg(svg)
    assert any("clip" in v.lower() for v in violations)


def test_text_element_is_a_violation():
    svg = _svg('<text x="0" y="5">hi</text>')
    violations = validate_svg(svg)
    assert any("text" in v.lower() for v in violations)

    result = pdc_convert(svg)
    assert result.valid is False


def test_curve_path_command_is_a_violation():
    svg = _svg('<path d="M0,0 C1,1 2,2 3,3" fill="#000"/>')
    violations = validate_svg(svg)
    assert any("curve" in v.lower() for v in violations)
    assert any("'C'" in v for v in violations)


def test_non_translate_transform_is_a_violation():
    svg = _svg('<g transform="scale(2)"><rect x="0" y="0" width="2" height="2" fill="#000"/></g>')
    violations = validate_svg(svg)
    assert any("transform" in v.lower() for v in violations)


def test_malformed_xml_reports_violation_not_exception():
    violations = validate_svg("<svg><rect x=1></svg")
    assert violations
    assert "malformed" in violations[0].lower()


def test_clean_svg_has_no_violations():
    svg = _svg('<rect x="0" y="0" width="4" height="4" fill="#000"/>')
    assert validate_svg(svg) == []


# ---------------------------------------------------------------------------
# Odd-coordinate warning (non-fatal, distinct from hard violations).
# ---------------------------------------------------------------------------


def test_odd_coordinate_produces_warning_not_violation():
    # cx/cy on the exact half-pixel grid (3.5, 3.5) land on integer (3, 3)
    # after the svg2pdc -0.5 shift -- valid, but both coordinates are odd.
    svg = _svg('<circle cx="3.5" cy="3.5" r="2" fill="#FF0000"/>')
    result = pdc_convert(svg)
    assert result.valid is True
    assert result.violations == []
    assert any("odd coordinate" in w for w in result.warnings)


def test_even_coordinates_produce_no_odd_warning():
    svg = _svg('<circle cx="4" cy="4" r="2" fill="#FF0000"/>')
    result = pdc_convert(svg)
    assert result.valid is True
    assert result.warnings == []


def test_odd_coordinate_warnings_are_bounded():
    # Many odd points shouldn't produce an unbounded warning list.
    points = " ".join(f"{1.5 + i},1.5" for i in range(30))
    svg = _svg(f'<polyline points="{points}" stroke="#000000" stroke-width="1"/>')
    result = pdc_convert(svg)
    assert result.valid is True
    assert 0 < len(result.warnings) <= 8


@pytest.mark.parametrize("tag_svg", [
    '<line x1="0" y1="0" x2="4" y2="4" stroke="#000" stroke-width="1"/>',
    '<polyline points="0,0 4,0 4,4" stroke="#000" stroke-width="1"/>',
    '<polygon points="0,0 4,0 4,4" fill="#000"/>',
    '<path d="M0,0 L4,0 L4,4 Z" fill="#000"/>',
])
def test_all_supported_element_kinds_convert_cleanly(tag_svg):
    svg = _svg(tag_svg)
    result = pdc_convert(svg)
    assert result.valid is True
    assert result.num_commands == 1


# ---------------------------------------------------------------------------
# ADVERSARIAL (roadmap 2.3): out-of-range / non-finite coordinates must be
# reported as violations, never leak struct.error / ValueError / OverflowError.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "vb",
    [
        "0 0 1e20 10",       # width would overflow the int16 header field
        "0 0 NaN 10",        # non-finite width
        "0 0 Infinity 10",
        "0 0 1e400 10",      # parses to inf
        "0 0 10 -Infinity",  # non-finite height
    ],
)
def test_out_of_range_viewbox_dimension_is_violation_not_crash(vb):
    svg = f'<svg {SVG_NS} width="10" height="10" viewBox="{vb}">' \
          '<rect x="0" y="0" width="4" height="4" fill="#000"/></svg>'
    result = pdc_convert(svg)  # must not raise struct.error / ValueError
    assert result.valid is False
    assert result.pdc_bytes is None
    assert result.violations


def test_garbage_viewbox_does_not_crash():
    # A non-numeric viewBox token must not raise out of _get_size.
    svg = f'<svg {SVG_NS} width="10" height="10" viewBox="0 0 abc 10">' \
          '<rect x="0" y="0" width="4" height="4" fill="#000"/></svg>'
    result = pdc_convert(svg)  # must not raise
    assert result is not None


@pytest.mark.parametrize(
    "coord",
    ["1e20", "-1e20", "100000"],  # exceed signed-int16 point range
)
def test_huge_path_coordinate_is_violation_not_crash(coord):
    svg = _svg(f'<path d="M0,0 L{coord},{coord}" stroke="#000" stroke-width="1"/>')
    result = pdc_convert(svg)  # must not raise struct.error
    assert result.valid is False
    assert result.pdc_bytes is None
    assert any("coordinate" in v.lower() or "range" in v.lower() for v in result.violations)


@pytest.mark.parametrize("bad", ["NaN", "Infinity", "-Infinity"])
def test_non_finite_path_coordinate_is_violation_not_crash(bad):
    svg = _svg(f'<path d="M {bad},0 L1,1" stroke="#000" stroke-width="1"/>')
    result = pdc_convert(svg)  # must not raise
    assert result.valid is False
    assert result.violations


def test_huge_rect_dimension_is_violation_not_crash():
    svg = _svg('<rect x="0" y="0" width="90000" height="4" fill="#000"/>')
    result = pdc_convert(svg)
    assert result.valid is False
    assert result.violations


@pytest.mark.parametrize("r", ["1e20", "70000", "-5", "NaN", "Infinity"])
def test_out_of_range_circle_radius_is_violation_not_crash(r):
    svg = _svg(f'<circle cx="4" cy="4" r="{r}" fill="#000"/>')
    result = pdc_convert(svg)  # must not raise struct.error
    assert result.valid is False
    assert result.violations


# ---------------------------------------------------------------------------
# ADVERSARIAL: deeply nested groups must not blow the Python recursion stack.
# ---------------------------------------------------------------------------


def test_deeply_nested_groups_is_violation_not_recursion_error():
    depth = 5000
    svg = (
        f"<svg {SVG_NS} width='10' height='10'>"
        + "<g>" * depth
        + '<rect x="0" y="0" width="2" height="2" fill="#000"/>'
        + "</g>" * depth
        + "</svg>"
    )
    violations = validate_svg(svg)  # must not raise RecursionError
    assert any("deep" in v.lower() or "nesting" in v.lower() for v in violations)

    result = pdc_convert(svg)  # end-to-end must not raise either
    assert result.valid is False


# ---------------------------------------------------------------------------
# ADVERSARIAL: XML hardening. These prove the stdlib parser is not vulnerable
# to entity-expansion (billion laughs) or external-entity (XXE) attacks; they
# guard against a future switch to an unsafe parser.
# ---------------------------------------------------------------------------


def test_billion_laughs_does_not_hang_or_oom():
    # Classic exponential entity expansion. Python's expat has built-in
    # billion-laughs amplification protection, so this returns promptly with a
    # normal result (here: an "undefined entity" parse error surfaced as a
    # violation) instead of consuming unbounded CPU/memory.
    entities = "\n".join(
        f'<!ENTITY a{i} "{("&a" + str(i - 1) + ";") * 10 if i else "dos"}">'
        for i in range(12)
    )
    svg = (
        f'<?xml version="1.0"?>\n<!DOCTYPE svg [\n{entities}\n]>\n'
        f'<svg {SVG_NS} width="10" height="10">'
        '<rect x="0" y="0" width="4" height="4" fill="#000"><desc>&a11;</desc></rect></svg>'
    )
    # Must return (not hang, not raise MemoryError); result is simply invalid.
    result = pdc_convert(svg)
    assert result.valid is False


def test_external_entity_xxe_is_not_resolved():
    # A SYSTEM external entity must never be fetched/expanded. Python's expat
    # leaves it undefined -> parse error -> reported as a violation.
    svg = (
        '<?xml version="1.0"?>\n'
        '<!DOCTYPE svg [<!ENTITY xxe SYSTEM "file:///etc/hostname">]>\n'
        f'<svg {SVG_NS} width="10" height="10">'
        '<rect x="0" y="0" width="4" height="4" fill="#000"><desc>&xxe;</desc></rect></svg>'
    )
    violations = validate_svg(svg)
    assert violations
    assert any("entity" in v.lower() or "malformed" in v.lower() for v in violations)
