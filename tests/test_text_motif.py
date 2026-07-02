"""Text-as-motif: deterministic whole-string font pipeline."""

import pytest

from app.adapters import motif_resolver
from app.adapters.llm import _validate_spec_facets
from app.adapters.motif_resolver import resolve_motifs
from app.engine.candidates import generate_candidate_set
from app.engine.composition import compose
from app.engine.seamless import Instance, clone_instances
from app.motifs import glyph_builder as gb
from app.motifs.registry import MOTIFS, get_motif
from app.validate.intent import validate_intent

TILE = 48.0


def _intent(size_mm: float = 16.0) -> dict:
    return {
        "intent_version": 1,
        "canvas": {"tile_mm": TILE, "dpi": 300},
        "seed": 7,
        "production": {"method": "print", "max_colors": 12},
        "palette": {
            "slots": [
                {"id": "ground", "hex": "#ffffff"},
                {"id": "ink", "hex": "#101010"},
                {"id": "accent", "hex": "#1040e0"},
            ]
        },
        "colorways": [
            {
                "id": "default",
                "name": "default",
                "mapping": {
                    "ground": "#ffffff",
                    "ink": "#101010",
                    "accent": "#1040e0",
                },
            },
            {
                "id": "inverse",
                "name": "inverse",
                "mapping": {
                    "ground": "#101010",
                    "ink": "#ffffff",
                    "accent": "#ff8800",
                },
            },
        ],
        "layers": [
            {
                "id": "bg",
                "type": "background",
                "z_order": 0,
                "params": {"color": "ground"},
            },
            {
                "id": "title",
                "type": "motif",
                "z_order": 1,
                "params": {"motif_id": "title", "size_mm": size_mm, "color": "ink"},
                "placement": {
                    "type": "point_set",
                    "point_set": {"points": [[24, 24]]},
                },
            },
        ],
    }


def _resolve(specs, intent=None, **kw):
    return resolve_motifs(intent or _intent(), specs, store=None, seed=7, **kw)


def _motif_layers(resolved):
    return [layer for layer in resolved["layers"] if layer.get("type") == "motif"]


def _build(text, segments=None, slots=None):
    return gb.build_text_motif(
        text,
        segments,
        default_color="ink",
        valid_color_slots=slots or {"ink", "accent"},
    )


def _composable(resolved):
    res = validate_intent(resolved, repair=False)
    return res.intent, res.palette, "default"


def test_text_motif_build_is_deterministic():
    a = _build("T").motif_id
    b = _build("T").motif_id
    assert a == b and a.startswith("recraft-")


def test_four_scripts_all_build():
    for ch in "T되中あ":  # Latin / Hangul / Hanzi / Hiragana
        assert _build(ch).motif_id.startswith("recraft-")


def test_whitespace_and_missing_glyphs():
    assert gb._glyph_outline(ord(" ")) is not None
    assert gb._glyph_outline(ord("🎨")) is None
    with pytest.raises(ValueError):
        _build(" ")
    result = _build("A🎨B")
    assert any("no glyph" in w for w in result.warnings)


def test_segments_make_multicolor_text_motif():
    result = _build(
        "Title",
        [{"text": "T", "scale": 1.5, "color": "accent"}, {"text": "itle"}],
    )
    plain = _build("Title")
    assert result.motif_id != plain.motif_id
    assert result.colors == {"s0": "accent", "s1": "ink"}


def test_unknown_color_slot_falls_back_with_warning():
    result = _build("Hi", [{"text": "Hi", "color": "nope"}], slots={"ink"})
    assert result.color == "ink" and result.colors is None
    assert any("not in palette" in w for w in result.warnings)


def test_unrenderable_segment_does_not_claim_color_slot():
    result = _build("🎨A", [{"text": "🎨", "color": "accent"}, {"text": "A"}])
    assert result.color == "ink" and result.colors is None
    assert any("no glyph" in w for w in result.warnings)


def test_resolver_keeps_one_text_layer():
    resolved = _resolve([{"layer_id": "title", "text": "Title"}])
    glyphs = _motif_layers(resolved)
    assert len(glyphs) == 1
    assert glyphs[0]["id"] == "title"
    assert glyphs[0]["params"]["motif_id"].startswith("recraft-")
    assert glyphs[0]["params"]["color"] == "ink"
    validate_intent(resolved, repair=False)


def test_resolver_maps_segment_colors_to_params_colors():
    resolved = _resolve(
        [
            {
                "layer_id": "title",
                "text": "AB",
                "segments": [
                    {"text": "A", "color": "accent"},
                    {"text": "B", "color": "ink"},
                ],
            }
        ]
    )
    params = _motif_layers(resolved)[0]["params"]
    assert "color" not in params
    assert params["colors"] == {"s0": "accent", "s1": "ink"}
    validate_intent(resolved, repair=False)


def test_resolved_text_is_seamless_zero_clones():
    resolved = _resolve([{"layer_id": "title", "text": "Title"}])
    layer = _motif_layers(resolved)[0]
    pt = layer["placement"]["point_set"]["points"][0]
    insts = [Instance(float(pt[0]), float(pt[1]), 0.0)]
    out = clone_instances(
        insts,
        motif=get_motif(layer["params"]["motif_id"]),
        size_mm=layer["params"]["size_mm"],
        tile_mm=TILE,
    )
    assert len(out) == 1


def test_compose_is_byte_identical():
    specs = [{"layer_id": "title", "text": "되고"}]
    a = compose(*_composable(_resolve(specs)))
    b = compose(*_composable(_resolve(specs)))
    assert a == b


def test_colorway_swap_recolors_segments():
    resolved = _resolve(
        [
            {
                "layer_id": "title",
                "text": "AB",
                "segments": [
                    {"text": "A", "color": "accent"},
                    {"text": "B", "color": "ink"},
                ],
            }
        ]
    )
    res = validate_intent(resolved, repair=False)
    default = compose(res.intent, res.palette, "default")
    inverse = compose(res.intent, res.palette, "inverse")
    assert default != inverse
    assert "#1040e0" in default and "#ff8800" in inverse


def test_diversification_preserves_text_motif():
    resolved = _resolve([{"layer_id": "title", "text": "Hi"}])
    motif_id = _motif_layers(resolved)[0]["params"]["motif_id"]
    result = generate_candidate_set(
        [resolved], candidate_count=4, seed=7, registry_version="test"
    )
    assert result.candidates
    assert motif_id in result.candidates[0].candidate.svg


def test_long_text_stays_one_layer():
    resolved = _resolve([{"layer_id": "title", "text": "M" * 100}])
    assert len(_motif_layers(resolved)) == 1
    validate_intent(resolved, repair=False)


def test_text_spec_valid_without_subject_scope():
    spec = {"layer_id": "title", "text": "Hi"}
    assert _validate_spec_facets([spec]) == []
    assert spec["subject"] == "text" and spec["scope"] == "whole"


def test_text_spec_rejects_bad_text_and_segments():
    assert _validate_spec_facets([{"layer_id": "t", "text": ""}])
    assert _validate_spec_facets([{"layer_id": "t", "text": "x", "segments": "no"}])
    assert _validate_spec_facets(
        [{"layer_id": "t", "text": "x", "source_image_index": 0}], image_count=1
    )
    assert _validate_spec_facets(
        [{"layer_id": "t", "text": "x", "segments": [{"text": "a", "scale": -1}]}]
    )
    assert _validate_spec_facets(
        [{"layer_id": "t", "text": "x", "segments": [{"text": ""}]}]
    )


def test_text_path_never_calls_recraft(monkeypatch):
    def _boom(*a, **k):
        raise AssertionError("text path must not call Recraft")

    monkeypatch.setattr(motif_resolver, "generate_via_recraft", _boom)
    monkeypatch.setattr(motif_resolver, "vectorize_via_recraft", _boom)
    resolved = _resolve([{"layer_id": "title", "text": "Ok"}])
    assert len(_motif_layers(resolved)) == 1
