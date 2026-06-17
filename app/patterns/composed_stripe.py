"""Composed stripe grounds with optional edge lines."""

import math

from app.api.schemas.common import (
    LinePosition,
    LineStyle,
    StripeBand,
    StripeLine,
    StripeLineDotShape,
)
from app.domain.colorway import Colorway
from app.domain.pattern import Pattern
from app.domain.repeat import RepeatMode
from app.domain.units import fmt


class ComposedStripePattern(Pattern):
    repeat_default = RepeatMode.block
    _EPSILON = 1e-9

    def __init__(
        self,
        tile_mm: float,
        background_color: str,
        stripes: list[StripeBand],
        angle: float = 0.0,
    ):
        colors = [background_color]
        self._background_color_index = 0
        self._stripe_color_indexes: list[int] = []
        self._line_color_indexes: list[list[int]] = []
        for stripe in stripes:
            self._stripe_color_indexes.append(len(colors))
            colors.append(stripe.color)
            line_indexes = []
            for line in stripe.edge_lines:
                line_indexes.append(len(colors))
                colors.append(line.color)
            self._line_color_indexes.append(line_indexes)
        super().__init__(tile_mm, Colorway(colors), RepeatMode.block)
        self.stripes = stripes
        self.angle = angle
        radians = math.radians(angle)
        self._nx = math.cos(radians)
        self._ny = math.sin(radians)
        self._dx = -self._ny
        self._dy = self._nx
        self._pattern_w = self._axis_period(self._nx)
        self._pattern_h = self._axis_period(self._ny)
        self._line_length = math.hypot(self._pattern_w, self._pattern_h) * 2
        self._phase_min, self._phase_max = self._phase_range()

    def _axis_period(self, component: float) -> float:
        if abs(component) <= self._EPSILON:
            return self.tile_mm
        return self.tile_mm / abs(component)

    def base_size(self) -> tuple[float, float]:
        return self._pattern_w, self._pattern_h

    def _line_x(self, stripe: StripeBand, position: LinePosition, offset: float) -> float:
        if position == LinePosition.start:
            return stripe.offset_mm + offset
        if position == LinePosition.end:
            return stripe.offset_mm + stripe.width_mm + offset
        return stripe.offset_mm + stripe.width_mm / 2 + offset

    def _phase_range(self) -> tuple[float, float]:
        phases = [
            0.0,
            self._nx * self._pattern_w,
            self._ny * self._pattern_h,
            self._nx * self._pattern_w + self._ny * self._pattern_h,
        ]
        return min(phases), max(phases)

    def _phase_centers(self, center: float) -> list[float]:
        start = math.floor((self._phase_min - center) / self.tile_mm) - 1
        end = math.ceil((self._phase_max - center) / self.tile_mm) + 1
        return [center + i * self.tile_mm for i in range(start, end + 1)]

    def _solid_line(self, phase: float, width: float, color: str) -> str:
        cx = self._nx * phase
        cy = self._ny * phase
        x1 = cx - self._dx * self._line_length
        y1 = cy - self._dy * self._line_length
        x2 = cx + self._dx * self._line_length
        y2 = cy + self._dy * self._line_length
        return (
            f'<line x1="{fmt(x1)}" y1="{fmt(y1)}" x2="{fmt(x2)}" '
            f'y2="{fmt(y2)}" stroke="{color}" stroke-width="{fmt(width)}" '
            f'stroke-linecap="butt"/>'
        )

    def _dotted_line(self, phase: float, line: StripeLine, color: str) -> str:
        assert line.dot_length_mm is not None and line.gap_mm is not None
        parts = []
        pitch = line.dot_length_mm + line.gap_mm
        cx = self._nx * phase
        cy = self._ny * phase
        t = -self._line_length
        while t < self._line_length:
            segment = min(line.dot_length_mm, self._line_length - t)
            if line.dot_shape == StripeLineDotShape.circle:
                r = min(line.width_mm, segment) / 2
                dot_cx = cx + self._dx * (t + segment / 2)
                dot_cy = cy + self._dy * (t + segment / 2)
                parts.append(
                    f'<circle cx="{fmt(dot_cx)}" cy="{fmt(dot_cy)}" '
                    f'r="{fmt(r)}" fill="{color}"/>'
                )
            else:
                x1 = cx + self._dx * t
                y1 = cy + self._dy * t
                x2 = cx + self._dx * (t + segment)
                y2 = cy + self._dy * (t + segment)
                parts.append(
                    f'<line x1="{fmt(x1)}" y1="{fmt(y1)}" x2="{fmt(x2)}" '
                    f'y2="{fmt(y2)}" stroke="{color}" '
                    f'stroke-width="{fmt(line.width_mm)}" stroke-linecap="butt"/>'
                )
            t += pitch
        return "".join(parts)

    def motif(self) -> str:
        background_color = self.colorway[self._background_color_index]
        parts = [
            f'<rect x="0" y="0" width="{fmt(self._pattern_w)}" '
            f'height="{fmt(self._pattern_h)}" fill="{background_color}"/>'
        ]
        for stripe_index, stripe in enumerate(self.stripes):
            stripe_color = self.colorway[self._stripe_color_indexes[stripe_index]]
            for phase in self._phase_centers(stripe.offset_mm + stripe.width_mm / 2):
                band = self._solid_line(phase, stripe.width_mm, stripe_color)
                if stripe.opacity < 1:
                    band = f'<g opacity="{fmt(stripe.opacity)}">{band}</g>'
                parts.append(band)
            for line_index, line in enumerate(stripe.edge_lines):
                line_color = self.colorway[
                    self._line_color_indexes[stripe_index][line_index]
                ]
                center = self._line_x(stripe, line.position, line.offset_mm)
                for phase in self._phase_centers(center):
                    if line.style == LineStyle.dotted:
                        parts.append(self._dotted_line(phase, line, line_color))
                    else:
                        parts.append(self._solid_line(phase, line.width_mm, line_color))
        return "".join(parts)
