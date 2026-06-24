import math
import xml.etree.ElementTree as ET

import pytest

from app.engine.host import Centerline, resolve_lane
from app.engine.intent import Band, StripeParams
from app.engine.palette import ColorSlot, Colorway, Palette
from app.engine.primitives import Background, Stripe, build_primitive, build_stripe
from app.validate.intent import validate_intent
from tests.test_intent import mvp_intent


def make_palette() -> Palette:
    return Palette(
        slots=(ColorSlot(id="ground", hex="#10243a"), ColorSlot(id="accent", hex="#ef8a7a")),
        colorways=(
            Colorway(id="default", mapping={"ground": "#10243a", "accent": "#ef8a7a"}),
            Colorway(id="alt", mapping={"ground": "#000000", "accent": "#ffffff"}),
        ),
    )


def stripe_params() -> StripeParams:
    return StripeParams(
        angle=-32, period_mm=24, bands=[Band(offset_mm=6, width_mm=12, color="accent")]
    )


# --- background --------------------------------------------------------------


def test_background_fills_resolved_color():
    frag = Background(color_slot="ground").render(48, make_palette(), "default")
    el = ET.fromstring(frag)
    assert el.tag == "rect"
    assert el.get("fill") == "#10243a"
    assert el.get("width") == "48" and el.get("height") == "48"


def test_background_is_colorway_aware():
    bg = Background(color_slot="ground")
    assert 'fill="#000000"' in bg.render(48, make_palette(), "alt")
    assert 'fill="#10243a"' in bg.render(48, make_palette(), None)  # None -> default


# --- stripe geometry ---------------------------------------------------------


def test_stripe_renders_bands_at_snapped_angle():
    s = build_stripe(stripe_params(), 48)
    g = ET.fromstring(s.render(make_palette(), "default"))
    assert g.tag == "g"
    lines = g.findall("line")
    assert lines, "stripe should draw at least one band line"
    for ln in lines:
        assert ln.get("stroke") == "#ef8a7a"
        assert ln.get("stroke-width") == "12"
    ln = lines[0]
    dx = float(ln.get("x2")) - float(ln.get("x1"))
    dy = float(ln.get("y2")) - float(ln.get("y1"))
    # Endpoints are serialized via fmt (4 decimals), so allow rounding slack.
    assert math.isclose(math.degrees(math.atan2(dy, dx)), s.snapped.angle_deg, abs_tol=1e-3)


def test_negative_diagonal_renders_visual_up_right():
    s = build_stripe(
        StripeParams(
            angle=-45,
            period_mm=48 / math.sqrt(2),
            bands=[Band(offset_mm=0, width_mm=8, color="accent")],
        ),
        48,
    )
    ln = ET.fromstring(s.render(make_palette(), "default")).findall("line")[0]
    assert float(ln.get("x2")) > float(ln.get("x1"))
    assert float(ln.get("y2")) < float(ln.get("y1"))


def test_stripe_render_is_deterministic():
    s = build_stripe(stripe_params(), 48)
    assert s.render(make_palette(), "default") == s.render(make_palette(), "default")


def test_build_primitive_dispatch_and_motif_unsupported():
    by_id = {layer.id: layer for layer in validate_intent(mvp_intent()).intent.layers}
    assert isinstance(build_primitive(by_id["ground"], 48), Background)
    assert isinstance(build_primitive(by_id["stripe_base"], 48), Stripe)
    with pytest.raises(ValueError):
        build_primitive(by_id["circle_on_stripe"], 48)  # motif -> session 3


# --- lanes() host contract ---------------------------------------------------


def test_stripe_lanes_contract():
    s = build_stripe(stripe_params(), 48)
    lanes = s.lanes()
    ids = {lane.id for lane in lanes}
    assert {"b0.start", "b0.center", "b0.end", "start", "center", "end"} <= ids

    center = resolve_lane(lanes, "center")
    assert math.isclose(center.centerline_path.offset_mm, 6 + 12 / 2)  # offset + width/2
    end = resolve_lane(lanes, "end")
    assert math.isclose(end.centerline_path.offset_mm, 6 + 12)  # trailing edge

    assert math.isclose(center.spacing_mm, 24) and center.phase_mm == 0.0
    # The snapped angle has a single source: lane property == centerline == stripe.
    assert center.angle_deg == s.snapped.angle_deg == center.centerline_path.angle_deg


def test_resolve_lane_unknown_raises():
    s = build_stripe(stripe_params(), 48)
    with pytest.raises(ValueError):
        resolve_lane(s.lanes(), "nope")


def test_multi_band_has_no_bare_aliases():
    params = StripeParams(
        angle=0,
        period_mm=24,
        bands=[
            Band(offset_mm=0, width_mm=6, color="accent"),
            Band(offset_mm=12, width_mm=6, color="accent"),
        ],
    )
    s = build_stripe(params, 48)
    ids = {lane.id for lane in s.lanes()}
    assert "b1.center" in ids
    assert "center" not in ids  # ambiguous across bands -> not registered
    with pytest.raises(ValueError):
        resolve_lane(s.lanes(), "center")


def test_lanes_independent_of_band_color():
    # path_following depends on the lane contract, not the host's color/fill.
    a = build_stripe(stripe_params(), 48).lanes()
    recolored = StripeParams(
        angle=-32, period_mm=24, bands=[Band(offset_mm=6, width_mm=12, color="ground")]
    )
    b = build_stripe(recolored, 48).lanes()
    assert [(x.id, x.centerline_path.offset_mm, x.spacing_mm) for x in a] == [
        (y.id, y.centerline_path.offset_mm, y.spacing_mm) for y in b
    ]


# --- Centerline arc-length sampling (consumer API locked for sessions 3/5) ----


def test_centerline_point_at_straight():
    cl = Centerline(angle_deg=0.0, offset_mm=10.0, p=0, q=1)
    (x, y), tangent = cl.point_at(5.0, 48.0)
    assert math.isclose(x, 5.0) and math.isclose(y, 10.0)
    assert tangent == 0.0


def test_centerline_wraps_on_torus():
    cl = Centerline(angle_deg=0.0, offset_mm=0.0, p=0, q=1)
    (x, _), _ = cl.point_at(50.0, 48.0)  # 50 mod 48
    assert math.isclose(x, 2.0)


def test_centerline_length_straight():
    cl = Centerline(angle_deg=math.degrees(math.atan2(5, 8)), offset_mm=0.0, p=5, q=8)
    assert math.isclose(cl.length_mm(48.0), 48.0 * math.hypot(5, 8))


def test_centerline_wave_displaces_perpendicular():
    cl = Centerline(
        angle_deg=0.0, offset_mm=0.0, kind="wave", wavelength_mm=24.0, amplitude_mm=4.0
    )
    assert cl.length_mm(48.0) == pytest.approx(48.0)
    # at wavelength/4 the perpendicular sinusoid is at its crest (= amplitude).
    (x, y), _ = cl.point_at(6.0, 48.0)
    assert x == pytest.approx(6.0)
    assert y == pytest.approx(4.0)
