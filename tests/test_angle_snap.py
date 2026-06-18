import math

import pytest

from app.engine.units import MAX_LANE_PERIOD_TILES, snap_angle

TILE = 48.0
PERIOD = 24.0


def test_snaps_minus_32_to_commensurate_rational():
    r = snap_angle(-32.0, TILE, PERIOD)
    assert (r.p, r.q) == (-5, 8)  # nearest rational slope within the denominator cap
    assert math.isclose(r.angle_deg, math.degrees(math.atan2(-5, 8)))
    assert abs(r.deviation_deg) < 0.05
    assert r.q <= MAX_LANE_PERIOD_TILES


def test_snapped_lane_wraps_after_integer_tiles():
    # Commensurability: advancing q tiles in x advances exactly p tiles in y.
    r = snap_angle(-32.0, TILE, PERIOD)
    assert math.isclose(r.q * math.tan(math.radians(r.angle_deg)), r.p, abs_tol=1e-9)


def test_slope_is_lowest_terms():
    r = snap_angle(-32.0, TILE, PERIOD)
    assert math.gcd(abs(r.p), r.q) == 1


def test_zero_stays_horizontal():
    r = snap_angle(0.0, TILE, PERIOD)
    assert (r.p, r.q) == (0, 1)
    assert r.angle_deg == 0.0
    assert r.deviation_deg == 0.0


@pytest.mark.parametrize("deg,p,q", [(45.0, 1, 1), (-45.0, -1, 1)])
def test_45_degrees_is_exact(deg, p, q):
    r = snap_angle(deg, TILE, PERIOD)
    assert (r.p, r.q) == (p, q)
    assert math.isclose(r.angle_deg, deg)
    assert math.isclose(r.deviation_deg, 0.0, abs_tol=1e-9)


@pytest.mark.parametrize("deg", [90.0, 89.9, -90.0])
def test_vertical_and_near_vertical(deg):
    r = snap_angle(deg, TILE, PERIOD)
    assert r.q == 0  # vertical lane
    assert r.angle_deg == 90.0


def test_sign_preserved_for_negative():
    r = snap_angle(-32.0, TILE, PERIOD)
    assert r.angle_deg < 0 and r.p < 0


@pytest.mark.parametrize("deg", [-90.0, 180.0, 270.0, -180.0])
def test_deviation_is_reduced_mod_180(deg):
    # Lane directions are mod 180; an axis-aligned request out of (-90, 90]
    # has ~zero real snap deviation despite the normalized result.
    assert abs(snap_angle(deg, TILE, PERIOD).deviation_deg) < 1e-9


def test_deviation_small_for_equivalent_out_of_range_request():
    # 148 deg is the same line direction as -32 deg (mod 180).
    assert abs(snap_angle(148.0, TILE, PERIOD).deviation_deg) < 0.05


@pytest.mark.parametrize("deg", [87.0, 88.0, 89.0, 89.5, 89.95])
def test_near_vertical_sweep_stays_valid(deg):
    r = snap_angle(deg, TILE, PERIOD)
    if r.q == 0:
        assert r.angle_deg == 90.0  # snapped to vertical
    else:
        assert math.gcd(abs(r.p), r.q) == 1 and r.q <= MAX_LANE_PERIOD_TILES


def test_exact_rational_input_is_fixed_point():
    base = math.degrees(math.atan2(1, 2))  # arctan(1/2)
    r = snap_angle(base, TILE, PERIOD)
    assert (r.p, r.q) == (1, 2)
    assert math.isclose(r.deviation_deg, 0.0, abs_tol=1e-9)


def test_idempotent():
    r1 = snap_angle(-32.0, TILE, PERIOD)
    r2 = snap_angle(r1.angle_deg, TILE, PERIOD)
    assert (r2.p, r2.q) == (r1.p, r1.q)
    assert math.isclose(r2.angle_deg, r1.angle_deg)


def test_deterministic_same_inputs():
    assert snap_angle(-32.0, TILE, PERIOD) == snap_angle(-32.0, TILE, PERIOD)
