import pytest
from fastapi.testclient import TestClient

from app.engine.palette import (
    ColorSlot,
    Colorway,
    PALETTES,
    Palette,
    is_hex_color,
    out_of_gamut,
)
from app.main import app

client = TestClient(app)


def _palette() -> Palette:
    slots = (ColorSlot("ground", "#10243a"), ColorSlot("accent", "#ef8a7a"))
    colorways = (
        Colorway("default", {"ground": "#10243a", "accent": "#ef8a7a"}),
        Colorway("autumn", {"ground": "#2b1a10", "accent": "#d98f3a"}, name="autumn"),
    )
    return Palette(slots=slots, colorways=colorways)


def test_color_slot_validates_hex():
    ColorSlot("ok", "#abc")
    with pytest.raises(ValueError):
        ColorSlot("bad", "not-a-color")


def test_resolve_color_uses_active_colorway():
    p = _palette()
    assert p.resolve_color("accent", "default") == "#ef8a7a"
    assert p.resolve_color("accent", "autumn") == "#d98f3a"


def test_colorway_mapping_is_immutable():
    cw = _palette().colorway("default")
    with pytest.raises(TypeError):
        cw.mapping["ground"] = "#ffffff"


def test_resolve_color_defaults_when_colorway_absent():
    p = _palette()
    assert p.resolve_color("ground", None) == p.resolve_color("ground", "default")


def test_resolve_color_rejects_unknown_slot():
    with pytest.raises(ValueError):
        _palette().resolve_color("nope", "default")


def test_palette_requires_default_colorway():
    with pytest.raises(ValueError):
        Palette(
            slots=(ColorSlot("a", "#fff"),),
            colorways=(Colorway("x", {"a": "#fff"}),),
        )


def test_colorway_must_map_all_slots():
    with pytest.raises(ValueError):
        Palette(
            slots=(ColorSlot("a", "#fff"), ColorSlot("b", "#000")),
            colorways=(Colorway("default", {"a": "#fff"}),),
        )


def test_distinct_colors_counts_per_colorway():
    assert _palette().distinct_colors("default") == {"#10243a", "#ef8a7a"}


def test_hex_color_validation():
    assert is_hex_color("#fff")
    assert is_hex_color("#ffffff")
    assert not is_hex_color("ffffff")


def test_out_of_gamut_flags_neon_only():
    assert out_of_gamut("#00ff00")
    assert not out_of_gamut("#f5ca57")


def test_list_palettes():
    resp = client.get("/api/v1/palettes")
    assert resp.status_code == 200
    assert set(resp.json()) == set(PALETTES)
