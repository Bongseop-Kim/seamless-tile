"""Unit tests for the candidate diversification engine (no HTTP)."""

import pytest

from app.engine.candidates import (
    _RESERVED_TONE_SLOT,
    _ground_kind_sibling,
    _with_stripe_rhythm,
    generate_candidate_set,
    generate_candidates,
)
from app.engine.determinism import layout_id_for
from app.motifs.registry import TEXTURE_MOTIFS
from app.engine.seamless import assert_seamless_invariants
from app.validate.intent import IntentInvalid, validate_intent
from tests.test_intent import mvp_intent


def lattice_intent() -> dict:
    return {
        "intent_version": 1,
        "canvas": {"tile_mm": 48, "dpi": 300},
        "seed": 7,
        "production": {"method": "digital", "max_colors": 12},
        "palette": {
            "slots": [
                {"id": "ground", "hex": "#10243a"},
                {"id": "accent", "hex": "#ef8a7a"},
            ]
        },
        "colorways": [
            {"id": "default", "mapping": {"ground": "#10243a", "accent": "#ef8a7a"}},
            {"id": "alt", "mapping": {"ground": "#222222", "accent": "#88cc88"}},
        ],
        "layers": [
            {"id": "ground", "type": "background", "z_order": 0, "params": {"color": "ground"}},
            {
                "id": "dots",
                "type": "motif",
                "z_order": 1,
                "params": {"motif_id": "circle", "size_mm": 2.0, "color": "accent"},
                "placement": {
                    "type": "lattice",
                    "lattice": {
                        "cell_w_mm": 12,
                        "cell_h_mm": 12,
                        "drop_fraction": 0.5,
                        "drop_axis": "column",
                    },
                },
            },
        ],
    }


def test_layout_id_is_seed_and_colorway_independent():
    base = validate_intent(mvp_intent()).intent
    a = layout_id_for(base.model_copy(update={"seed": 1}))
    b = layout_id_for(base.model_copy(update={"seed": 2}))
    alternate_colorways = [
        base.colorways[0].model_copy(
            update={
                "id": "alternate",
                "mapping": {"ground": "#ffffff", "accent": "#000000", "gold": "#888888"},
            }
        )
    ]
    c = layout_id_for(base.model_copy(update={"colorways": alternate_colorways}))
    assert a == b  # seed is not part of the layout identity
    assert a == c  # colorway data is not part of the layout identity


def test_lattice_yields_drop_fraction_layouts():
    cs = generate_candidates(lattice_intent(), candidate_count=4)
    # Symmetry variants are disabled; lattice spacing/drop/size still diversify.
    assert cs.available_strategy_count >= 4
    layouts = [c.candidate.layout_id for c in cs.candidates]
    assert len(cs.candidates) == 4
    assert len(set(layouts)) == 4
    assert all(c.intent.symmetry is None for c in cs.candidates)


def test_single_colorway_lattice_still_returns_four_design_candidates():
    intent = lattice_intent()
    intent["colorways"] = [intent["colorways"][0]]
    cs = generate_candidates(intent, candidate_count=4)

    assert len(cs.candidates) == 4
    assert all(c.intent.symmetry is None for c in cs.candidates)
    variants = {
        (
            c.intent.layers[1].placement.lattice.cell_w_mm,
            c.intent.layers[1].placement.lattice.drop_fraction,
            c.intent.layers[1].params.size_mm,
        )
        for c in cs.candidates
    }
    assert len(variants) == 4


def test_stripe_candidates_vary_width_and_count_without_symmetry():
    intent = mvp_intent()
    intent["layers"] = intent["layers"][:2]
    cs = generate_candidates(intent, candidate_count=4)

    assert len(cs.candidates) == 4
    assert all(c.intent.symmetry is None for c in cs.candidates)
    stripe_params = [c.intent.layers[1].params for c in cs.candidates]
    # Diversifies along period AND band structure (rhythm presets), so the candidates
    # carry >= 2 distinct stripe signatures (period + per-band offset/width layout).
    signatures = {
        (p.period_mm, tuple((b.offset_mm, b.width_mm) for b in p.bands))
        for p in stripe_params
    }
    assert len(signatures) >= 2


def test_dedup_keeps_only_distinct_svgs():
    cs = generate_candidates(lattice_intent(), candidate_count=8)
    svgs = [c.candidate.svg for c in cs.candidates]
    assert len(set(svgs)) == len(svgs)


def test_candidates_are_rank_sorted():
    cs = generate_candidates(lattice_intent(), candidate_count=4)
    keys = [c.rank_key for c in cs.candidates]
    assert keys == sorted(keys)


def test_count_one_has_no_diversity_warning():
    cs = generate_candidates(mvp_intent(), candidate_count=1)
    assert len(cs.candidates) == 1
    assert all("diversity" not in w for w in cs.warnings)


def test_unknown_colorway_raises():
    import pytest

    with pytest.raises(ValueError):
        generate_candidates(mvp_intent(), candidate_count=2, colorway="nope")


def _single_band_stripe_intent() -> dict:
    intent = mvp_intent()
    intent["layers"] = intent["layers"][:2]  # ground + single-band stripe
    return intent


def _two_band_stripe_intent() -> dict:
    intent = _single_band_stripe_intent()
    intent["layers"][1]["params"]["bands"] = [
        {"offset_mm": 0, "width_mm": 3.0, "color": "accent"},
        {"offset_mm": 4.8, "width_mm": 3.0, "color": "gold"},
    ]
    return intent


# --- Option C: stripe band-rhythm variants -----------------------------------


def test_stripe_rhythm_presets_present_and_seamless():
    cs = generate_candidates(_single_band_stripe_intent(), candidate_count=8)
    band_counts = {len(c.intent.layers[1].params.bands) for c in cs.candidates}
    assert 3 in band_counts  # a multi-band rhythm preset surfaced
    for c in cs.candidates:
        assert_seamless_invariants(c.intent)  # must not raise


def test_rhythm_partitions_one_period_exactly():
    base = validate_intent(_single_band_stripe_intent()).intent
    variant = _with_stripe_rhythm(base, 1, (1.0, 2.0, 3.0), 1.0)
    params = variant.layers[1].params
    last = params.bands[-1]
    assert abs((last.offset_mm + last.width_mm) - params.period_mm) < 1e-3
    offsets = [b.offset_mm for b in params.bands]
    assert offsets == sorted(offsets) and len(set(offsets)) == len(offsets)


def test_rhythm_multi_band_cycles_existing_colors():
    base = validate_intent(_two_band_stripe_intent()).intent
    variant = _with_stripe_rhythm(base, 1, (3.0, 2.0, 2.0), 0.0)
    colors = [b.color for b in variant.layers[1].params.bands]
    assert colors == ["accent", "gold", "accent"]


def test_stripe_rhythm_introduces_no_new_colors():
    intent = _single_band_stripe_intent()
    base = validate_intent(intent).intent
    base_colors = {b.color for b in base.layers[1].params.bands}
    cs = generate_candidates(intent, candidate_count=8)
    for c in cs.candidates:
        assert {b.color for b in c.intent.layers[1].params.bands} <= base_colors
        assert c.intent.palette == base.palette
        assert c.intent.colorways == base.colorways


# --- bg_texture toggle --------------------------------------------------------


def _object_repeat_ground_intent() -> dict:
    intent = _single_band_stripe_intent()
    intent["layers"][0]["params"].update(
        {
            "kind": "object_repeat",
            "motif_id": "twill",
            "cell_mm": 8,
            "texture_color": "tone",
        }
    )
    intent["palette"]["slots"].append({"id": "tone", "hex": "#33405e"})
    for cw in intent["colorways"]:
        cw["mapping"]["tone"] = "#33405e"
    return intent


def test_ground_kind_toggle_yields_with_and_without():
    # vary_ground adds a sibling design; round-robin surfaces both solid and object_repeat.
    cs = generate_candidate_set(
        [_single_band_stripe_intent()], candidate_count=4, vary_ground=True
    )
    kinds = {c.intent.layers[0].params.kind for c in cs.candidates}
    assert "solid" in kinds and "object_repeat" in kinds


def test_solid_ground_gains_object_repeat_sibling():
    # A solid ground sibling becomes an object_repeat tonal texture: a built-in motif and
    # a NEW derived tone slot, mapped in every colorway, distinct from the ground hex.
    raw = _single_band_stripe_intent()
    sibling = _ground_kind_sibling(raw)
    assert sibling is not None
    intent = validate_intent(sibling).intent
    bg = intent.layers[0].params
    assert bg.kind == "object_repeat"
    assert bg.motif_id in TEXTURE_MOTIFS
    assert bg.texture_color == _RESERVED_TONE_SLOT
    slot_ids = {s.id for s in intent.palette.slots}
    assert _RESERVED_TONE_SLOT in slot_ids
    for cw in intent.colorways:
        assert _RESERVED_TONE_SLOT in cw.mapping
        assert cw.mapping[_RESERVED_TONE_SLOT] != cw.mapping[bg.color]  # tonal != ground
    assert_seamless_invariants(intent)


def test_object_repeat_ground_flattens_to_solid_sibling():
    # The reverse toggle: an object_repeat ground yields a plain solid sibling.
    raw = _object_repeat_ground_intent()
    sibling = _ground_kind_sibling(raw)
    assert sibling is not None
    bg = validate_intent(sibling).intent.layers[0].params
    assert bg.kind == "solid"
    assert bg.motif_id is None and bg.texture_color is None


# --- Multi-design orchestration (generate_candidate_set) ----------------------


def test_candidate_set_single_equals_generate_candidates():
    single = mvp_intent()
    a = generate_candidates(single, candidate_count=4)
    b = generate_candidate_set([single], candidate_count=4)
    assert [c.candidate.svg for c in a.candidates] == [
        c.candidate.svg for c in b.candidates
    ]
    assert [c.id for c in a.candidates] == [c.id for c in b.candidates]


def test_candidate_set_spans_designs():
    cs = generate_candidate_set([mvp_intent(), lattice_intent()], candidate_count=4)
    assert len(cs.candidates) == 4
    assert {c.design_index for c in cs.candidates} == {0, 1}
    assert len({c.id for c in cs.candidates}) == len(cs.candidates)
    keys = [c.rank_key for c in cs.candidates]
    assert keys == sorted(keys)


def test_candidate_set_drops_invalid_design():
    cs = generate_candidate_set([{"intent_version": 1}, mvp_intent()], candidate_count=4)
    assert cs.candidates
    assert all(c.design_index == 1 for c in cs.candidates)
    assert any("dropped" in w for w in cs.warnings)


def test_candidate_set_all_invalid_raises():
    with pytest.raises(IntentInvalid):
        generate_candidate_set(
            [{"intent_version": 1}, {"foo": "bar"}], candidate_count=4
        )


def test_candidate_set_deterministic():
    designs = [mvp_intent(), lattice_intent()]
    a = generate_candidate_set(designs, candidate_count=4)
    b = generate_candidate_set(designs, candidate_count=4)
    assert [c.candidate.svg for c in a.candidates] == [
        c.candidate.svg for c in b.candidates
    ]
    assert [c.id for c in a.candidates] == [c.id for c in b.candidates]


# --- Ground texture: motifs, tonal color, anti-clip sizing, stripe ratios -----


def test_ground_texture_motifs_render_seamless():
    from app.engine.composition import compose

    for motif_id in ("diamond", "square", "twill", "herringbone"):
        intent = _single_band_stripe_intent()
        intent["layers"].append(
            {
                "id": "tex",
                "type": "motif",
                "z_order": 5,
                "params": {"motif_id": motif_id, "size_mm": 2.0, "color": "accent"},
                "placement": {
                    "type": "lattice",
                    "lattice": {"cell_w_mm": 4.0, "cell_h_mm": 4.0},
                },
            }
        )
        res = validate_intent(intent)
        assert_seamless_invariants(res.intent)
        assert "<svg" in compose(res.intent, res.palette, "default")


def test_derive_tonal_hex_behavior():
    from app.engine.palette import derive_tonal_hex

    dark = derive_tonal_hex("#000080", 0.12)
    assert dark.startswith("#") and len(dark) == 7 and dark != "#000080"
    assert derive_tonal_hex("#000080", 0.12) == dark  # deterministic
    assert derive_tonal_hex("19-4024 TCX", 0.12) == "19-4024 TCX"  # non-hex passthrough


def test_ground_kind_sibling_skips_non_hex_ground():
    raw = _single_band_stripe_intent()
    raw["colorways"][0]["mapping"]["ground"] = "19-4024 TCX"  # spot color, not hex
    assert _ground_kind_sibling(raw) is None


def test_ground_kind_sibling_skips_malformed_colorway():
    raw = _single_band_stripe_intent()
    del raw["colorways"][0]["id"]
    assert _ground_kind_sibling(raw) is None


def test_stripe_presets_are_uneven():
    from app.engine.candidates import _STRIPE_RHYTHMS_MULTI, _STRIPE_RHYTHMS_SINGLE

    for _name, weights, _gap in _STRIPE_RHYTHMS_SINGLE + _STRIPE_RHYTHMS_MULTI:
        assert len(set(weights)) > 1  # not all-equal widths
