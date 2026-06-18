"""Host geometry contract: the interface placement depends on, not the host's internals.

A host primitive (currently ``stripe``) exposes ``lanes() -> [LaneField]``. Each
``LaneField`` carries a curve-extensible ``Centerline`` that consumers walk by arc
length. ``path_following`` (session 3) and curved/wave lanes (session 5) reuse this
exact contract -- they call ``Centerline.point_at`` rather than reaching into the
host's band geometry.
"""

import math
from dataclasses import dataclass
from typing import Literal, Protocol

Point = tuple[float, float]  # (x_mm, y_mm) in torus coordinates


@dataclass(frozen=True)
class Centerline:
    """A lane centerline, parametrized by arc length and periodic on the torus.

    ``straight`` lanes are defined by ``angle_deg`` (the snapped ``arctan(p/q)``
    direction) and ``offset_mm`` (perpendicular distance from the tile origin); the
    snapped slope ``(p, q)`` gives the closure length. ``wave`` lanes (session 5)
    add ``wavelength_mm`` / ``amplitude_mm`` and reuse the same consumer API.

    The descriptor returns raw, unrounded floats; formatting via ``units.fmt`` is the
    renderer's job at the serialization boundary.
    """

    angle_deg: float
    offset_mm: float
    p: int = 0
    q: int = 1
    kind: Literal["straight", "wave"] = "straight"
    wavelength_mm: float | None = None
    amplitude_mm: float | None = None

    def length_mm(self, tile_mm: float) -> float:
        """Arc length of one torus period until the lane closes (assumes a square tile)."""
        if self.kind != "straight":
            raise NotImplementedError(f"length_mm not implemented for kind={self.kind!r}")
        return tile_mm * math.hypot(self.p, self.q)

    def point_at(self, s_mm: float, tile_mm: float) -> tuple[Point, float]:
        """Sample the centerline at arc length ``s_mm``.

        Returns ``((x_mm, y_mm) on the torus, tangent_angle_deg)``. For a straight
        lane the tangent is constant (the lane angle), which is exactly what
        ``rotation: "follow_path"`` consumes.
        """
        if self.kind != "straight":
            raise NotImplementedError(f"point_at not implemented for kind={self.kind!r}")
        a = math.radians(self.angle_deg)
        dx, dy = math.cos(a), math.sin(a)
        nx, ny = -math.sin(a), math.cos(a)  # unit normal; offset runs along it
        x = self.offset_mm * nx + s_mm * dx
        y = self.offset_mm * ny + s_mm * dy
        return (x % tile_mm, y % tile_mm), self.angle_deg


@dataclass(frozen=True)
class LaneField:
    """A named lane a host exposes for path-following placement."""

    id: str
    centerline_path: Centerline
    spacing_mm: float
    phase_mm: float

    @property
    def angle_deg(self) -> float:
        # Derived from the centerline so the snapped angle has a single source.
        return self.centerline_path.angle_deg


class HostLayer(Protocol):
    def lanes(self) -> list[LaneField]: ...


def resolve_lane(lanes: list[LaneField], key: str) -> LaneField:
    """Select a lane by exact ``LaneField.id``.

    Hosts register the bare keywords as lane ids only when unambiguous (e.g. a
    single-band stripe), so a bare keyword on a multi-band host falls through to a
    clear error listing the available ids.
    """
    for lane in lanes:
        if lane.id == key:
            return lane
    available = ", ".join(lane.id for lane in lanes)
    raise ValueError(f"unknown lane {key!r}; available: {available}")
