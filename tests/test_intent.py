import copy

import pytest

from app.engine.determinism import ReproMeta, seeded_rng, sorted_layers
from app.validate.intent import IntentInvalid, validate_intent


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


def test_mvp_intent_is_valid():
    result = validate_intent(mvp_intent())
    assert result.intent.intent_version == 1
    assert len(result.intent.layers) == 4
    assert result.warnings == []


def test_unknown_host_layer_rejected():
    intent = mvp_intent()
    intent["layers"][2]["placement"]["host_layer"] = "does_not_exist"
    with pytest.raises(IntentInvalid):
        validate_intent(intent)


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
    with pytest.raises(IntentInvalid):
        validate_intent(intent)


def test_color_count_over_max_rejected_for_screen():
    intent = mvp_intent()
    intent["production"] = {"method": "screen", "max_colors": 2}  # 3 colors > 2
    with pytest.raises(IntentInvalid):
        validate_intent(intent)


def test_color_count_not_enforced_for_digital():
    intent = mvp_intent()
    intent["production"] = {"method": "digital", "max_colors": 2}
    # digital jobs are not color-limited
    assert validate_intent(intent).intent.production.max_colors == 2


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
    meta = ReproMeta.build(intent_version=1, seed=42, colorway_id="default")
    assert meta.engine_version and meta.registry_version
    assert meta.seed == 42 and meta.colorway_id == "default"
