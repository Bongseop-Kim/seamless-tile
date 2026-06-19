import math

import pytest

from app.engine.host import Centerline, LaneField
from app.engine.intent import Placement
from app.engine.placement import Instance, place_path_following


class FakeHost:
    """A minimal HostLayer exposing only lanes() — no stripe internals.

    Proves path_following depends on the host *contract*, not on any concrete
    primitive's representation.
    """

    def __init__(self, lanes: list[LaneField]) -> None:
        self._lanes = lanes

    def lanes(self) -> list[LaneField]:
        return self._lanes


def _lane(
    lane_id: str = "center",
    *,
    angle_deg: float = 45.0,
    offset_mm: float = 0.0,
    p: int = 1,
    q: int = 1,
    spacing_mm: float = 10.0,
) -> LaneField:
    centerline = Centerline(angle_deg=angle_deg, offset_mm=offset_mm, p=p, q=q)
    return LaneField(
        id=lane_id, centerline_path=centerline, spacing_mm=spacing_mm, phase_mm=0.0
    )


def _placement(**kwargs) -> Placement:
    kwargs.setdefault("type", "path_following")
    kwargs.setdefault("lane", "center")
    return Placement(**kwargs)


def test_contract_only_dependency_positions():
    from app.engine.units import snap_spacing

    tile = 48.0
    host = FakeHost([_lane(angle_deg=45.0, p=1, q=1)])
    instances = place_path_following(host, _placement(spacing_mm=12.0), tile)

    length = tile * math.hypot(1, 1)
    # 12.0 does not divide the diagonal closure, so the step is snapped to an exact
    # divisor for a uniform rhythm across the wrap (see snap_spacing / place_path_following).
    n, spacing_eff = snap_spacing(length, 12.0)
    assert len(instances) == n

    # s=0 on a 45 deg lane through the origin -> (0, 0)
    assert instances[0].x_mm == pytest.approx(0.0)
    assert instances[0].y_mm == pytest.approx(0.0)
    # s=spacing_eff -> (eff cos45, eff sin45) wrapped on the torus
    assert instances[1].x_mm == pytest.approx((spacing_eff * math.cos(math.radians(45))) % tile)
    assert instances[1].y_mm == pytest.approx((spacing_eff * math.sin(math.radians(45))) % tile)


def test_diagonal_spacing_snaps_for_uniform_wrap():
    """Regression: a diagonal lane's along-step is snapped so the wrap gap matches the
    interior gap (closure = tile*hypot(p,q) is irrational here, so raw 12.0 cannot)."""
    from app.engine.units import snap_spacing

    tile = 48.0
    spacing = 12.0
    host = FakeHost([_lane(angle_deg=45.0, p=1, q=1)])
    instances = place_path_following(host, _placement(spacing_mm=spacing), tile)

    closure = tile * math.hypot(1, 1)
    n, eff = snap_spacing(closure, spacing)
    # precondition of the bug: the requested step does NOT divide the closure
    assert round(closure / spacing) != pytest.approx(closure / spacing)
    # the snapped step divides the closure exactly -> n equal gaps, wrap included
    assert len(instances) == n
    assert eff * n == pytest.approx(closure)
    # the first interior gap (no torus wrap between the first two points) is the step
    gap = math.hypot(
        instances[1].x_mm - instances[0].x_mm,
        instances[1].y_mm - instances[0].y_mm,
    )
    assert gap == pytest.approx(eff)
    assert gap != pytest.approx(spacing)  # would equal raw spacing before the fix


def test_phase_mm_wraps_into_closure_period():
    """A6: phase_mm >= lane closure wraps (modulo L) instead of emitting an empty layer.

    Byte-identical for in-range phases (phase % L == phase), so determinism holds; the
    only behavior change is the out-of-range case that used to silently place nothing.
    """
    tile = 48.0
    host = FakeHost([_lane(angle_deg=0.0, p=0, q=1)])  # axis-aligned: closure L == tile
    closure = tile * math.hypot(0, 1)
    spacing = 6.0  # divides the closure exactly (no snap)

    in_range = place_path_following(host, _placement(spacing_mm=spacing, phase_mm=4.0), tile)
    wrapped = place_path_following(
        host, _placement(spacing_mm=spacing, phase_mm=4.0 + 3 * closure), tile
    )

    def pts(insts):
        return [
            (round(i.x_mm, 9), round(i.y_mm, 9), round(i.rotation_deg, 9)) for i in insts
        ]

    assert len(in_range) > 0  # sanity: in-range phase places instances
    assert pts(wrapped) == pts(in_range)  # out-of-range phase wraps to the same set


def test_rotation_follow_path_uses_tangent():
    host = FakeHost([_lane(angle_deg=30.0, p=1, q=2)])
    instances = place_path_following(
        host, _placement(spacing_mm=10.0, rotation="follow_path"), 48.0
    )
    assert instances
    assert all(inst.rotation_deg == pytest.approx(30.0) for inst in instances)


@pytest.mark.parametrize("rotation", [None, "fixed"])
def test_rotation_non_follow_is_zero(rotation):
    host = FakeHost([_lane(angle_deg=30.0, p=1, q=2)])
    kwargs = {"spacing_mm": 10.0}
    if rotation is not None:
        kwargs["rotation"] = rotation
    instances = place_path_following(host, _placement(**kwargs), 48.0)
    assert instances
    assert all(inst.rotation_deg == 0.0 for inst in instances)


def test_phase_offsets_first_instance():
    # Horizontal lane (angle 0, p=0,q=1) at offset 5: point_at(s) -> (s, 5).
    host = FakeHost([_lane(angle_deg=0.0, offset_mm=5.0, p=0, q=1)])
    instances = place_path_following(host, _placement(spacing_mm=10.0, phase_mm=3.0), 48.0)
    assert instances[0].x_mm == pytest.approx(3.0)
    assert instances[0].y_mm == pytest.approx(5.0)


def test_determinism_identical_lists():
    host = FakeHost([_lane(angle_deg=45.0, p=1, q=1)])
    placement = _placement(spacing_mm=7.0, phase_mm=1.0)
    first = place_path_following(host, placement, 48.0)
    second = place_path_following(host, placement, 48.0)
    assert first == second
    assert all(isinstance(inst, Instance) for inst in first)


def test_smaller_spacing_yields_more_instances():
    host = FakeHost([_lane(angle_deg=45.0, p=1, q=1)])
    fine = place_path_following(host, _placement(spacing_mm=6.0), 48.0)
    coarse = place_path_following(host, _placement(spacing_mm=24.0), 48.0)
    assert len(fine) > len(coarse)


def test_unknown_lane_raises():
    host = FakeHost([_lane(lane_id="center")])
    with pytest.raises(ValueError):
        place_path_following(host, _placement(lane="missing", spacing_mm=10.0), 48.0)


def test_works_with_real_stripe_lanes_contract():
    # The same code path must work against the real Stripe host via lanes() only.
    from app.engine.primitives.stripe import build_stripe
    from app.engine.intent import StripeParams, Band

    params = StripeParams(
        angle=-32, period_mm=24, bands=[Band(offset_mm=6, width_mm=12, color="accent")]
    )
    stripe = build_stripe(params, 48.0)
    instances = place_path_following(
        stripe, _placement(lane="center", spacing_mm=6.0), 48.0
    )
    assert len(instances) > 1
    angle = stripe.snapped.angle_deg
    follow = place_path_following(
        stripe, _placement(lane="center", spacing_mm=6.0, rotation="follow_path"), 48.0
    )
    assert all(inst.rotation_deg == pytest.approx(angle) for inst in follow)
