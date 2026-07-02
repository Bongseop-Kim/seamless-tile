import copy

import pytest
from pydantic import ValidationError

from app.engine.determinism import ReproMeta, seeded_rng, sorted_layers
from app.engine.intent import ScatterSpec
from app.engine.seamless import assert_seamless_invariants
from app.motifs.registry import MOTIFS, MotifDef, _ORIGIN, _UNIT_BBOX, _symbol
from app.validate.intent import IntentInvalid, validate_intent


def _register_test_motifs() -> None:
    """circle/bee are no longer shipped built-ins; the engine tests still exercise the
    seamless/composition/determinism machinery against fixed geometry, so re-register the
    same ids + verbatim geometry as TEST fixtures. Done at import time (not a conftest
    fixture) because the determinism subprocess tests import this module directly without
    pytest. ``setdefault`` keeps it idempotent and re-import-safe."""
    MOTIFS.setdefault(
        "circle",
        MotifDef(
            id="circle",
            symbol=_symbol("circle", '<circle cx="0" cy="0" r="0.5" fill="currentColor"/>'),
            bbox_mm=_UNIT_BBOX,
            anchor=_ORIGIN,
        ),
    )
    MOTIFS.setdefault(
        "bee",
        MotifDef(
            id="bee",
            symbol=_symbol(
                "bee",
                '<ellipse cx="0" cy="0" rx="0.22" ry="0.42" fill="currentColor"/>'
                '<ellipse cx="-0.3" cy="-0.1" rx="0.18" ry="0.28" fill="currentColor"/>'
                '<ellipse cx="0.3" cy="-0.1" rx="0.18" ry="0.28" fill="currentColor"/>',
            ),
            bbox_mm=_UNIT_BBOX,
            anchor=_ORIGIN,
        ),
    )


_register_test_motifs()


def mvp_intent() -> dict:
    """The session-4 MVP scenario: background + diagonal stripe + two motif lanes."""
    return {
        "intent_version": 1,
        "canvas": {"tile_mm": 48, "dpi": 300},
        "seed": 184231,
        "production": {"method": "digital", "max_colors": 12},
        "palette": {
            "slots": [
                {"id": "ground", "hex": "#10243a", "spot": "19-4024 TCX", "name": "navy"},
                {"id": "accent", "hex": "#ef8a7a"},
                {"id": "gold", "hex": "#f5ca57"},
            ]
        },
        "colorways": [
            {
                "id": "default",
                "name": "default",
                "mapping": {"ground": "#10243a", "accent": "#ef8a7a", "gold": "#f5ca57"},
            }
        ],
        "layers": [
            {"id": "ground", "type": "background", "z_order": 0, "params": {"color": "ground"}},
            {
                "id": "stripe_base",
                "type": "stripe",
                "z_order": 1,
                "params": {
                    # -36.87deg snaps to the 3-4-5 slope (p/q = -3/4); a diagonal
                    # stripe tiles only at period_mm = tile_mm / (k*hypot(p, q)) =
                    # 48 / (k*5), so period 9.6 (k=1) is seamless. width < period.
                    "angle": -36.87,
                    "period_mm": 9.6,
                    "bands": [{"offset_mm": 0, "width_mm": 4.8, "color": "accent"}],
                },
            },
            {
                "id": "circle_on_stripe",
                "type": "motif",
                "z_order": 2,
                "opacity": 1.0,
                "params": {"motif_id": "circle", "size_mm": 1.4, "color": "accent"},
                "placement": {
                    "type": "path_following",
                    "host_layer": "stripe_base",
                    "lane": "center",
                    "spacing_mm": 6,
                    "phase_mm": 0,
                },
            },
            {
                "id": "bee_on_stripe",
                "type": "motif",
                "z_order": 3,
                "params": {"motif_id": "bee", "size_mm": 5, "color": "gold"},
                "placement": {
                    "type": "path_following",
                    "host_layer": "stripe_base",
                    "lane": "end",
                    "spacing_mm": 24,
                    "phase_mm": 12,
                    "rotation": "follow_path",
                },
            },
        ],
    }


def test_palette_slot_count_capped():
    """A2: an over-cap list is a structural reject (max_length -> IntentInvalid)."""
    intent = mvp_intent()
    intent["palette"]["slots"] = [{"id": f"s{i}", "hex": "#000000"} for i in range(65)]
    with pytest.raises(IntentInvalid):
        validate_intent(intent)


def test_scatter_count_capped():
    """A2: scatter count is bounded so one intent cannot request unbounded work."""
    with pytest.raises(ValidationError):
        ScatterSpec(mode="poisson", min_dist_mm=1.0, count=10_001)


def test_tile_mm_ceiling_enforced():
    """A4: tile_mm is bounded on the generate path (was export-only)."""
    intent = mvp_intent()
    intent["canvas"]["tile_mm"] = 3000  # > max_tile_mm (2000)
    with pytest.raises(IntentInvalid) as exc:
        validate_intent(intent)
    assert "max_tile_mm" in str(exc.value)


def test_motif_size_exceeding_tile_rejected():
    """A7: size_mm > tile_mm breaks the clone precondition -> reject at stage-0."""
    intent = mvp_intent()
    intent["layers"][2]["params"]["size_mm"] = 60.0  # tile_mm is 48
    with pytest.raises(IntentInvalid) as exc:
        validate_intent(intent)
    assert "size_mm" in str(exc.value)


def test_mvp_intent_is_valid():
    result = validate_intent(mvp_intent())
    assert result.intent.intent_version == 1
    assert len(result.intent.layers) == 4
    assert result.warnings == []


def test_removed_top_level_arrangement_field_is_rejected():
    intent = mvp_intent()
    intent["sym" + "metry"] = {"kind": "removed"}
    with pytest.raises(IntentInvalid):
        validate_intent(intent)


def test_bare_lane_on_multi_band_stripe_normalized_to_band0():
    # An LLM emits a bare lane ("center") against a multi-band stripe, whose lanes are
    # namespaced (b0.center...). Without repair this fails deep in compose (unknown lane)
    # and drops every candidate -> opaque 500. Repair normalizes it to band 0.
    intent = mvp_intent()
    intent["layers"][1]["params"]["bands"] = [
        {"offset_mm": 0, "width_mm": 2.4, "color": "accent"},
        {"offset_mm": 4.8, "width_mm": 2.4, "color": "accent"},
    ]
    result = validate_intent(intent)
    lanes = [la.placement.lane for la in result.intent.layers if la.type == "motif"]
    assert lanes == ["b0.center", "b0.end"]
    assert any("normalized to 'b0.center'" in w for w in result.warnings)
    assert_seamless_invariants(result.intent)  # composes (was: unknown lane)


def test_unknown_host_layer_rejected():
    intent = mvp_intent()
    intent["layers"][2]["placement"]["host_layer"] = "does_not_exist"
    with pytest.raises(IntentInvalid):
        validate_intent(intent)


def test_path_following_host_must_be_stripe():
    # Only a stripe exposes lanes(); an LLM that hosts path_following on the background
    # would otherwise crash in compose (AttributeError: 'Background' has no 'lanes') -> 500.
    intent = mvp_intent()
    intent["layers"][2]["placement"]["host_layer"] = "ground"  # a background layer
    with pytest.raises(IntentInvalid) as exc:
        validate_intent(intent)
    assert "must be a stripe" in str(exc.value)


@pytest.mark.parametrize("field", ["host_layer", "lane", "spacing_mm"])
def test_path_following_requires_fields(field):
    intent = mvp_intent()
    intent["layers"][2]["placement"][field] = None
    with pytest.raises(IntentInvalid) as exc:
        validate_intent(intent)
    assert field in str(exc.value)


def test_period_not_dividing_tile_rejected():
    intent = mvp_intent()
    intent["layers"][1]["params"]["period_mm"] = 25  # 48 % 25 != 0
    # repair=True now snaps off-grid periods (see test_off_grid_stripe_period_is_snapped);
    # the invariant rejection is still enforced when repair is off.
    with pytest.raises(IntentInvalid):
        validate_intent(intent, repair=False)


def test_color_count_over_max_rejected_for_yarn_dyed():
    intent = mvp_intent()
    intent["production"] = {"method": "yarn_dyed", "max_colors": 2}  # 3 colors > 2
    with pytest.raises(IntentInvalid):
        validate_intent(intent)


def test_color_count_not_enforced_for_print():
    intent = mvp_intent()
    intent["production"] = {"method": "print", "max_colors": 2}
    # print jobs are not color-limited
    assert validate_intent(intent).intent.production.max_colors == 2


def test_legacy_method_digital_screen_coerced_to_print():
    intent = mvp_intent()
    # legacy print sub-methods map to "print" (backward compat) -> color count not enforced
    intent["production"] = {"method": "digital", "max_colors": 2}
    assert validate_intent(intent).intent.production.method == "print"
    intent["production"] = {"method": "screen", "max_colors": 2}
    assert validate_intent(intent).intent.production.method == "print"


def test_duplicate_layer_id_rejected():
    intent = mvp_intent()
    intent["layers"][3]["id"] = "stripe_base"
    with pytest.raises(IntentInvalid):
        validate_intent(intent)


def test_motif_requires_exactly_one_color_spec():
    both = mvp_intent()
    both["layers"][2]["params"]["colors"] = {"fill": "accent"}
    with pytest.raises(IntentInvalid):
        validate_intent(both)

    neither = mvp_intent()
    del neither["layers"][2]["params"]["color"]
    with pytest.raises(IntentInvalid):
        validate_intent(neither)


def test_negative_spacing_rejected():
    intent = mvp_intent()
    intent["layers"][2]["placement"]["spacing_mm"] = -6
    with pytest.raises(IntentInvalid):
        validate_intent(intent)


def test_path_following_spacing_snapped_with_warning():
    # The diagonal stripe snaps to 3-4-5, so the lane closure is 48*5 = 240. A step of 7
    # does not divide 240 (nor the tile 48): the engine snaps it and warns rather than
    # rejecting -- otherwise nearly all diagonal lanes would be unusable.
    intent = mvp_intent()
    intent["layers"][2]["placement"]["spacing_mm"] = 7
    result = validate_intent(intent)
    assert any("snapped" in w and "circle_on_stripe" in w for w in result.warnings)


def test_path_following_rejects_host_lane_and_standalone_path_together():
    intent = mvp_intent()
    intent["layers"][2]["placement"]["path"] = {"kind": "straight", "angle": 0}
    with pytest.raises(IntentInvalid, match="only one"):
        validate_intent(intent)


def test_path_following_rejects_partial_host_fields_with_standalone_path():
    intent = mvp_intent()
    intent["layers"][2]["placement"]["lane"] = None
    intent["layers"][2]["placement"]["path"] = {"kind": "straight", "angle": 0}
    with pytest.raises(IntentInvalid, match="only one"):
        validate_intent(intent)


def test_placement_rejects_spec_for_wrong_type():
    intent = mvp_intent()
    intent["layers"][2]["placement"]["lattice"] = {"cell_w_mm": 12, "cell_h_mm": 12}
    with pytest.raises(IntentInvalid, match="path_following"):
        validate_intent(intent)


def test_unknown_color_slot_rejected():
    intent = mvp_intent()
    intent["layers"][0]["params"]["color"] = "missing_slot"
    with pytest.raises(IntentInvalid):
        validate_intent(intent)


def test_dpi_clamped_on_repair():
    intent = mvp_intent()
    intent["canvas"]["dpi"] = 400
    result = validate_intent(intent, repair=True)
    assert result.intent.canvas.dpi == 300
    assert any("dpi" in w for w in result.warnings)


def test_dpi_rejected_without_repair():
    intent = mvp_intent()
    intent["canvas"]["dpi"] = 400
    with pytest.raises(IntentInvalid):
        validate_intent(intent, repair=False)


def test_unknown_top_level_field_rejected():
    intent = mvp_intent()
    intent["bogus"] = True
    with pytest.raises(IntentInvalid):
        validate_intent(intent)


def test_layer_order_is_stable_and_deterministic():
    result = validate_intent(mvp_intent())
    order = [layer.id for layer in sorted_layers(result.intent.layers)]
    assert order == ["ground", "stripe_base", "circle_on_stripe", "bee_on_stripe"]
    assert order == [layer.id for layer in sorted_layers(result.intent.layers)]


def test_validation_does_not_mutate_input():
    intent = mvp_intent()
    before = copy.deepcopy(intent)
    validate_intent(intent)
    assert intent == before


def test_color_resolution_is_repeatable_and_colorway_aware():
    result = validate_intent(mvp_intent())
    palette = result.palette
    assert palette.resolve_color("accent", "default") == "#ef8a7a"
    assert palette.resolve_color("accent", None) == palette.resolve_color("accent", "default")


def test_seeded_rng_is_deterministic():
    assert seeded_rng(7).random() == seeded_rng(7).random()


def test_repro_meta_carries_versions():
    meta = ReproMeta(intent_version=1, seed=42, colorway_id="default")
    assert meta.engine_version and meta.registry_version
    assert meta.seed == 42 and meta.colorway_id == "default"


def _full_coverage_stripe_intent() -> dict:
    """navy ground + a 3-band silver/gold/silver stripe whose bands fill the whole
    period (3.2*3 == 9.6) -> the stripe fully occludes the navy ground."""
    return {
        "intent_version": 1,
        "canvas": {"tile_mm": 48, "dpi": 300},
        "seed": 0,
        "production": {"method": "digital", "max_colors": 12},
        "palette": {
            "slots": [
                {"id": "navy", "hex": "#000080"},
                {"id": "silver", "hex": "#C0C0C0"},
                {"id": "gold", "hex": "#FFD700"},
            ]
        },
        "colorways": [
            {"id": "default", "mapping": {"navy": "#000080", "silver": "#C0C0C0", "gold": "#FFD700"}}
        ],
        "layers": [
            {"id": "ground", "type": "background", "z_order": 0, "params": {"color": "navy"}},
            {
                "id": "stripe",
                "type": "stripe",
                "z_order": 1,
                "params": {
                    "angle": -36.87,
                    "period_mm": 9.6,
                    "bands": [
                        {"offset_mm": 0.0, "width_mm": 3.2, "color": "silver"},
                        {"offset_mm": 3.2, "width_mm": 3.2, "color": "gold"},
                        {"offset_mm": 6.4, "width_mm": 3.2, "color": "silver"},
                    ],
                },
            },
        ],
    }


def test_full_coverage_stripe_over_background_is_repaired():
    res = validate_intent(_full_coverage_stripe_intent())
    bands = res.intent.layers[1].params.bands
    period = res.intent.layers[1].params.period_mm
    coverage = sum(b.width_mm for b in bands) / period
    assert coverage <= 0.75 + 1e-9  # bands no longer fill the period
    assert sum(b.width_mm for b in bands) < period  # a ground gap remains
    assert [b.color for b in bands] == ["silver", "gold", "silver"]  # colors unchanged
    assert len(bands) == 3  # band count unchanged
    assert any("covered the ground" in w for w in res.warnings)
    assert_seamless_invariants(res.intent)  # still tile-commensurate


def test_stripe_without_background_not_repaired():
    intent = _full_coverage_stripe_intent()
    intent["layers"] = [intent["layers"][1]]  # stripe only, no ground to protect
    intent["layers"][0]["z_order"] = 0
    res = validate_intent(intent)
    bands = res.intent.layers[0].params.bands
    assert sum(b.width_mm for b in bands) == pytest.approx(9.6)  # untouched


def test_stripe_under_cap_not_repaired():
    res = validate_intent(mvp_intent())  # single band 4.8/9.6 = 0.5 coverage
    bands = res.intent.layers[1].params.bands
    assert len(bands) == 1 and bands[0].width_mm == 4.8


def test_stripe_ground_gap_repair_skipped_without_repair_flag():
    res = validate_intent(_full_coverage_stripe_intent(), repair=False)
    bands = res.intent.layers[1].params.bands
    assert sum(b.width_mm for b in bands) == pytest.approx(9.6)  # not repaired


def test_stripe_ground_gap_repair_is_deterministic():
    a = validate_intent(_full_coverage_stripe_intent()).intent.layers[1].params.bands
    b = validate_intent(_full_coverage_stripe_intent()).intent.layers[1].params.bands
    assert [(x.offset_mm, x.width_mm) for x in a] == [(x.offset_mm, x.width_mm) for x in b]


def test_off_grid_stripe_period_is_snapped():
    intent = _full_coverage_stripe_intent()
    intent["layers"][1]["params"]["period_mm"] = 12.0  # not tile/(5k) for the 3-4-5 slope
    intent["layers"][1]["params"]["bands"] = [{"offset_mm": 0, "width_mm": 6.0, "color": "silver"}]
    res = validate_intent(intent)
    period = res.intent.layers[1].params.period_mm
    assert abs(period - 9.6) < 1e-6  # snapped to nearest commensurate (48/(5*1))
    assert any("snapped" in w for w in res.warnings)
    assert_seamless_invariants(res.intent)  # now tiles


def test_commensurate_stripe_period_not_snapped():
    res = validate_intent(mvp_intent())  # period 9.6 already valid
    assert res.intent.layers[1].params.period_mm == 9.6
    assert not any("snapped" in w for w in res.warnings)
