"""Stripe primitive: straight bands plus the host ``lanes()`` geometry contract.

The stripe snaps its requested angle to a tile-commensurate direction at build time
and exposes one band's leading edge / centerline / trailing edge as lanes. Seamless
tiling (boundary clones, ``<pattern>`` wrapping) is session 4; here we only serialize
the band geometry and the lane contract.
"""

import math
from dataclasses import dataclass

from app.engine.host import Centerline, LaneField
from app.engine.intent import StripeParams
from app.engine.palette import Palette
from app.engine.units import SnappedAngle, fmt, snap_angle
from app.render.svg import escape_attr


def build_stripe(params: StripeParams, tile_mm: float) -> "Stripe":
    return Stripe(
        params=params,
        tile_mm=tile_mm,
        snapped=snap_angle(params.angle, tile_mm, params.period_mm),
    )


@dataclass(frozen=True)
class Stripe:
    params: StripeParams
    tile_mm: float
    snapped: SnappedAngle

    def render(self, palette: Palette, colorway_id: str | None = None) -> str:
        """Serialize the straight bands at the snapped angle, spaced by period_mm.

        Each band is drawn as a stroked centerline repeated across the tile's
        perpendicular extent. Not yet clipped/cloned to a seamless tile (session 4).
        """
        angle = self.snapped.angle_deg
        a = math.radians(angle)
        dx, dy = math.cos(a), math.sin(a)
        nx, ny = -math.sin(a), math.cos(a)
        tile = self.tile_mm
        half_len = tile * 2.0  # long enough to span the tile at any angle
        # Perpendicular projection range of the tile's corners onto the band normal.
        projections = (0.0, tile * nx, tile * ny, tile * (nx + ny))
        lo, hi = min(projections), max(projections)
        period = self.params.period_mm

        parts: list[str] = []
        for band in self.params.bands:
            fill = escape_attr(palette.resolve_color(band.color, colorway_id))
            width = fmt(band.width_mm)
            center = band.offset_mm + band.width_mm / 2.0
            k_min = math.floor((lo - center) / period)
            k_max = math.ceil((hi - center) / period)
            for k in range(k_min, k_max + 1):
                offset = center + k * period
                cx, cy = offset * nx, offset * ny
                x1, y1 = cx - half_len * dx, cy - half_len * dy
                x2, y2 = cx + half_len * dx, cy + half_len * dy
                parts.append(
                    f'<line x1="{fmt(x1)}" y1="{fmt(y1)}" '
                    f'x2="{fmt(x2)}" y2="{fmt(y2)}" '
                    f'stroke="{fill}" stroke-width="{width}"/>'
                )
        return f"<g>{''.join(parts)}</g>"

    def lanes(self) -> list[LaneField]:
        """Expose per-band leading edge / centerline / trailing edge as lanes.

        For a single-band stripe the bare keywords ``start``/``center``/``end`` are
        also registered as ids so the MVP intent (``lane: "center"|"end"``) resolves.
        """
        p, q = self.snapped.p, self.snapped.q
        angle = self.snapped.angle_deg
        period = self.params.period_mm
        single = len(self.params.bands) == 1
        # spacing_mm exposes the band period; the lane's true closure period is
        # centerline_path.length_mm(tile) = tile*hypot(p, q) (enforced in session 4).

        lanes: list[LaneField] = []
        for i, band in enumerate(self.params.bands):
            edges = {
                "start": band.offset_mm,
                "center": band.offset_mm + band.width_mm / 2.0,
                "end": band.offset_mm + band.width_mm,
            }
            for name, offset in edges.items():
                centerline = Centerline(angle_deg=angle, offset_mm=offset, p=p, q=q)
                lanes.append(
                    LaneField(
                        id=f"b{i}.{name}",
                        centerline_path=centerline,
                        spacing_mm=period,
                        phase_mm=0.0,
                    )
                )
                if single:
                    lanes.append(
                        LaneField(
                            id=name,
                            centerline_path=centerline,
                            spacing_mm=period,
                            phase_mm=0.0,
                        )
                    )
        return lanes
