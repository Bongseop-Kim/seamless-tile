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
        """Closure length of one torus period (assumes a square tile).

        For ``wave`` lanes this is the along-axis closure length: the walk parameter
        is along-axis distance (an approximation of true arc length). Periodicity and
        seam continuity -- not exact along-curve spacing -- are the acceptance criteria
        (consistent with ``seamless.py``'s ``spacing|L`` note).
        """
        return tile_mm * math.hypot(self.p, self.q)

    def point_at(self, s_mm: float, tile_mm: float) -> tuple[Point, float]:
        """Sample the centerline at walk parameter ``s_mm``.

        Returns ``((x_mm, y_mm) on the torus, tangent_angle_deg)``. A ``straight`` lane
        has a constant tangent (the lane angle). A ``wave`` lane adds a perpendicular
        sinusoid ``amplitude*sin(2*pi*s/wavelength)``; its tangent varies along the
        curve, which is exactly what ``rotation: "follow_path"`` consumes. The lane is
        torus-periodic and seam-continuous only when the sinusoid returns to phase 0 at
        the closure length ``L = tile*hypot(p, q)`` -- i.e. ``wavelength | L`` (which
        equals ``wavelength | tile`` only for an axis-aligned lane). This is enforced by
        ``validate_intent``; ``point_at`` itself does not re-check it.
        """
        a = math.radians(self.angle_deg)
        dx, dy = math.cos(a), math.sin(a)
        nx, ny = -math.sin(a), math.cos(a)  # unit normal; offset runs along it
        x = self.offset_mm * nx + s_mm * dx
        y = self.offset_mm * ny + s_mm * dy
        if self.kind == "straight":
            return (x % tile_mm, y % tile_mm), self.angle_deg
        if self.kind == "wave":
            if self.wavelength_mm is None or self.amplitude_mm is None:
                raise ValueError(
                    "wave centerline requires wavelength_mm and amplitude_mm"
                )
            w = 2.0 * math.pi / self.wavelength_mm
            perp = self.amplitude_mm * math.sin(w * s_mm)
            perp_prime = self.amplitude_mm * w * math.cos(w * s_mm)
            x += perp * nx
            y += perp * ny
            vx = dx + perp_prime * nx
            vy = dy + perp_prime * ny
            tangent = math.degrees(math.atan2(vy, vx))
            return (x % tile_mm, y % tile_mm), tangent
        raise NotImplementedError(f"point_at not implemented for kind={self.kind!r}")


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
