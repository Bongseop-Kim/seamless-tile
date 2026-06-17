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
    tile = 48.0
    host = FakeHost([_lane(angle_deg=45.0, p=1, q=1)])
    instances = place_path_following(host, _placement(spacing_mm=12.0), tile)

    length = tile * math.hypot(1, 1)
    expected_n = math.ceil(length / 12.0)  # phase 0
    assert len(instances) == expected_n

    # s=0 on a 45 deg lane through the origin -> (0, 0)
    assert instances[0].x_mm == pytest.approx(0.0)
    assert instances[0].y_mm == pytest.approx(0.0)
    # s=12 -> (12 cos45, 12 sin45) wrapped on the torus
    assert instances[1].x_mm == pytest.approx((12.0 * math.cos(math.radians(45))) % tile)
    assert instances[1].y_mm == pytest.approx((12.0 * math.sin(math.radians(45))) % tile)


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
