"""Unit tests for the candidate diversification engine (no HTTP)."""

from app.engine.candidates import generate_candidates
from app.engine.determinism import layout_id_for
from app.validate.intent import validate_intent
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
    assert len({p.period_mm for p in stripe_params}) >= 2
    assert len({p.bands[0].width_mm for p in stripe_params}) >= 2


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
