"""Session-8 Recraft motif pipeline: authoring-time generate -> normalize -> hash-register.

All generation is mocked (no network). The runtime references the registered motif by
id only, so the same input yields the same motif_id (cache hit) and the same SVG.
"""

import pytest

from app.adapters import recraft
from app.adapters.recraft import RecraftError, RecraftNotConfigured, create_motif
from app.engine.generate import generate
from app.motifs.registry import MOTIFS, get_motif, normalize_motif_svg
from app.render.sanitize import SanitizeError
from tests.test_intent import mvp_intent


from tests._helpers import _svg
from tests._fakes import _ScriptedRecraft


def test_create_motif_normalizes_and_registers():
    client = _ScriptedRecraft(_svg('<circle cx="50" cy="50" r="40" fill="#ff0000"/>'))
    motif_id = create_motif("a red dot", client=client)

    assert motif_id.startswith("recraft-")
    motif = get_motif(motif_id)  # runtime lookup by id works
    assert motif.bbox_mm == (-0.5, -0.5, 0.5, 0.5)  # normalized unit box
    assert motif.anchor == (0.0, 0.0)
    assert "currentColor" in motif.symbol  # color -> slot reference
    assert "#ff0000" not in motif.symbol  # concrete color removed


def test_same_input_same_motif_id_cache_hit():
    client = _ScriptedRecraft(_svg('<circle cx="50" cy="50" r="40" fill="#ff0000"/>'))
    first = create_motif("p", client=client)
    second = create_motif("p", client=client)
    assert first == second
    assert len(client.calls) == 1  # second served from the per-prompt cache


def test_distinct_prompts_same_shape_collide_by_content_hash():
    # Different prompts; the generator returns the same shape with a different color.
    # Normalization recolors to currentColor, so the geometry — and thus the content
    # hash id — is identical.
    c1 = _ScriptedRecraft(_svg('<circle cx="50" cy="50" r="40" fill="#ff0000"/>'))
    c2 = _ScriptedRecraft(_svg('<circle cx="50" cy="50" r="40" fill="#00ff00"/>'))
    a = create_motif("first", client=c1, use_cache=False)
    b = create_motif("second", client=c2, use_cache=False)
    assert a == b


def test_runtime_determinism_same_motif_same_symbol():
    raw = _svg('<path d="M0 0 L100 0 L50 100 Z" fill="#123456"/>')
    motif_id = create_motif("triangle", client=_ScriptedRecraft(raw))
    # An INDEPENDENT normalization of the same input must reproduce the id and the exact
    # symbol bytes the registry holds — a real determinism check (not x == x).
    fresh = normalize_motif_svg(raw)
    assert fresh.id == motif_id
    assert fresh.symbol == get_motif(motif_id).symbol


def test_recraft_motif_composes_and_passes_output_gate():
    client = _ScriptedRecraft(_svg('<circle cx="50" cy="50" r="40" fill="#345678"/>'))
    motif_id = create_motif("a dot", client=client)

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


def test_create_motif_propagates_unsafe_svg():
    client = _ScriptedRecraft(_svg('<script>alert(1)</script>'))
    with pytest.raises(SanitizeError):
        create_motif("evil", client=client, use_cache=False)


def test_create_motif_unconfigured_raises():
    with pytest.raises(RecraftNotConfigured):
        create_motif("x", use_cache=False)


def test_create_motif_wraps_client_failure():
    class _Boom:
        def generate(self, prompt: str) -> str:
            raise RuntimeError("generator down")

    with pytest.raises(RecraftError):
        create_motif("x", client=_Boom(), use_cache=False)


@pytest.mark.parametrize("viewbox", ["0 0 0 0", "0 0 -100 100", "0 0 100 -100", "0 0 -100 -100"])
def test_intake_rejects_nonpositive_viewbox(viewbox):
    # Zero -> would divide by zero; negative -> silent mirror/off-box. Both must be a
    # clean ValueError (the documented intake failure), not a crash or bad geometry.
    svg = _svg('<circle cx="1" cy="1" r="1" fill="#abc"/>', viewbox=viewbox)
    with pytest.raises(ValueError):
        normalize_motif_svg(svg)


def test_create_motif_surfaces_viewbox_valueerror_not_zerodiv():
    client = _ScriptedRecraft(_svg('<circle cx="1" cy="1" r="1" fill="#abc"/>', viewbox="0 0 0 0"))
    with pytest.raises(ValueError):  # not an uncaught ZeroDivisionError
        create_motif("degenerate", client=client, use_cache=False)


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


def test_create_motif_rejects_empty_prompt():
    client = _ScriptedRecraft(_svg('<circle cx="1" cy="1" r="1" fill="#abc"/>'))
    with pytest.raises(ValueError):
        create_motif("   ", client=client, use_cache=False)
    assert len(client.calls) == 0  # rejected before the generator is invoked
