"""Session-8 Recraft motif intake: normalize -> hash-register -> compose gate.

All generation is mocked (no network). The runtime references the registered motif by
id only, so the same input yields the same motif_id and the same SVG.
"""

import pytest

from app.engine.generate import generate
from app.motifs.registry import get_motif, normalize_motif_svg, register_motif
from app.render.sanitize import SanitizeError
from tests.test_intent import mvp_intent

from tests._helpers import _svg


def test_normalize_and_register():
    motif_id = register_motif(
        normalize_motif_svg(_svg('<circle cx="50" cy="50" r="40" fill="#ff0000"/>'))
    )

    assert motif_id.startswith("recraft-")
    motif = get_motif(motif_id)  # runtime lookup by id works
    assert motif.bbox_mm == (-0.5, -0.5, 0.5, 0.5)  # normalized unit box
    assert motif.anchor == (0.0, 0.0)
    assert "currentColor" in motif.symbol  # color -> slot reference
    assert "#ff0000" not in motif.symbol  # concrete color removed


def test_same_shape_collides_by_content_hash():
    # Same shape with a different color: normalization recolors to currentColor, so the
    # geometry — and thus the content hash id — is identical.
    a = register_motif(normalize_motif_svg(_svg('<circle cx="50" cy="50" r="40" fill="#ff0000"/>')))
    b = register_motif(normalize_motif_svg(_svg('<circle cx="50" cy="50" r="40" fill="#00ff00"/>')))
    assert a == b


def test_runtime_determinism_same_motif_same_symbol():
    raw = _svg('<path d="M0 0 L100 0 L50 100 Z" fill="#123456"/>')
    motif_id = register_motif(normalize_motif_svg(raw))
    # An INDEPENDENT normalization of the same input must reproduce the id and the exact
    # symbol bytes the registry holds — a real determinism check (not x == x).
    fresh = normalize_motif_svg(raw)
    assert fresh.id == motif_id
    assert fresh.symbol == get_motif(motif_id).symbol


def test_recraft_motif_composes_and_passes_output_gate():
    motif_id = register_motif(
        normalize_motif_svg(_svg('<circle cx="50" cy="50" r="40" fill="#345678"/>'))
    )

    intent = mvp_intent()
    for layer in intent["layers"]:
        if layer["id"] == "circle_on_stripe":
            layer["params"]["motif_id"] = motif_id
    # compose() re-runs the sanitize gate; a registered recraft motif must pass it.
    svg = generate(intent).svg
    assert f"motif-{motif_id}" in svg
    assert "<pattern" in svg


@pytest.mark.parametrize(
    "inner",
    [
        '<image href="http://evil/x.png"/>',  # embedded raster
        '<filter id="f"/>',  # filter
        '<use href="http://evil"/>',  # external href
        "<script>alert(1)</script>",  # script
    ],
)
def test_intake_rejects_unsafe_geometry(inner):
    with pytest.raises(SanitizeError):
        normalize_motif_svg(_svg(inner))


@pytest.mark.parametrize("viewbox", ["0 0 0 0", "0 0 -100 100", "0 0 100 -100", "0 0 -100 -100"])
def test_intake_rejects_nonpositive_viewbox(viewbox):
    # Zero -> would divide by zero; negative -> silent mirror/off-box. Both must be a
    # clean ValueError (the documented intake failure), not a crash or bad geometry.
    svg = _svg('<circle cx="1" cy="1" r="1" fill="#abc"/>', viewbox=viewbox)
    with pytest.raises(ValueError):
        normalize_motif_svg(svg)


@pytest.mark.parametrize(
    "inner",
    [
        "",  # no children at all
        '<defs><circle cx="50" cy="50" r="40" fill="#abc"/></defs>',  # only non-rendering
    ],
)
def test_intake_rejects_no_drawable_geometry(inner):
    with pytest.raises(ValueError):
        normalize_motif_svg(_svg(inner))


def test_intake_accepts_geometry_inside_group():
    # A renderable element nested in a <g> still counts as drawable.
    motif = normalize_motif_svg(_svg('<g><rect x="0" y="0" width="10" height="10" fill="#abc"/></g>'))
    assert motif.id.startswith("recraft-")
