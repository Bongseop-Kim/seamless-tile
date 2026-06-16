"""Dot patterns: legacy single polka dots or layered dot fields."""

from app.api.schemas.dot import DotLayer, DotShape
from app.domain.colorway import Colorway
from app.domain.pattern import Pattern
from app.domain.repeat import RepeatMode
from app.domain.units import fmt


class DotPattern(Pattern):
    repeat_default = RepeatMode.half_drop

    def __init__(
        self,
        spacing_mm: float | None,
        radius_mm: float | None,
        colorway: Colorway,
        repeat: RepeatMode | None = None,
        tile_mm: float | None = None,
        layers: list[DotLayer] | None = None,
    ):
        super().__init__(tile_mm or spacing_mm or 1, colorway, repeat)
        self.spacing_mm = spacing_mm
        self.radius_mm = radius_mm
        self.layers = layers or []

    def base_size(self) -> tuple[float, float]:
        if self.layers:
            return self.tile_mm, self.tile_mm
        return super().base_size()

    def _bg_color(self) -> str:
        if self.layers:
            return self.colorway[0]
        return self.colorway[1] if len(self.colorway) > 1 else "#ffffff"

    def _layer_offsets(self, layer: DotLayer) -> list[tuple[float, float]]:
        if layer.repeat == RepeatMode.half_drop:
            return [(0.0, 0.0), (layer.spacing_x_mm / 2, layer.spacing_y_mm / 2)]
        if layer.repeat == RepeatMode.brick:
            return [(0.0, 0.0), (layer.spacing_x_mm / 2, layer.spacing_y_mm)]
        return [(0.0, 0.0)]

    def _shape(self, cx: float, cy: float, layer: DotLayer, color: str) -> str:
        size = layer.size_mm
        half = size / 2
        if layer.shape == DotShape.circle:
            return (
                f'<circle cx="{fmt(cx)}" cy="{fmt(cy)}" r="{fmt(half)}" '
                f'fill="{color}"/>'
            )
        if layer.shape == DotShape.square:
            return (
                f'<rect x="{fmt(cx - half)}" y="{fmt(cy - half)}" '
                f'width="{fmt(size)}" height="{fmt(size)}" fill="{color}"/>'
            )
        if layer.shape == DotShape.diamond:
            points = [
                (cx, cy - half),
                (cx + half, cy),
                (cx, cy + half),
                (cx - half, cy),
            ]
            value = " ".join(f"{fmt(x)},{fmt(y)}" for x, y in points)
            return f'<polygon points="{value}" fill="{color}"/>'
        d = (
            f"M {fmt(cx)} {fmt(cy - half)} "
            f"C {fmt(cx + half)} {fmt(cy - half)} {fmt(cx + half)} {fmt(cy)} "
            f"{fmt(cx)} {fmt(cy + half)} "
            f"C {fmt(cx - half)} {fmt(cy)} {fmt(cx - half)} {fmt(cy - half)} "
            f"{fmt(cx)} {fmt(cy - half)} Z"
        )
        return f'<path d="{d}" fill="{color}"/>'

    def _start_position(self, offset: float, spacing: float) -> float:
        return offset % spacing - spacing

    def _layer(self, layer: DotLayer, color: str) -> str:
        parts = []
        for dx, dy in self._layer_offsets(layer):
            x = self._start_position(layer.offset_x_mm + dx, layer.spacing_x_mm)
            while x <= self.tile_mm + layer.spacing_x_mm + 1e-9:
                y = self._start_position(layer.offset_y_mm + dy, layer.spacing_y_mm)
                while y <= self.tile_mm + layer.spacing_y_mm + 1e-9:
                    parts.append(self._shape(x, y, layer, color))
                    y += layer.spacing_y_mm
                x += layer.spacing_x_mm
        return "".join(parts)

    def motif(self) -> str:
        if self.layers:
            bg = (
                f'<rect x="0" y="0" width="{fmt(self.tile_mm)}" '
                f'height="{fmt(self.tile_mm)}" fill="{self._bg_color()}"/>'
            )
            dots = "".join(
                self._layer(layer, self.colorway[i + 1])
                for i, layer in enumerate(self.layers)
            )
            return bg + dots

        assert self.spacing_mm is not None and self.radius_mm is not None
        s = self.spacing_mm
        bg = (
            f'<rect x="0" y="0" width="{fmt(s)}" height="{fmt(s)}" '
            f'fill="{self._bg_color()}"/>'
        )
        dot = (
            f'<circle cx="{fmt(s / 2)}" cy="{fmt(s / 2)}" '
            f'r="{fmt(self.radius_mm)}" fill="{self.colorway[0]}"/>'
        )
        return bg + dot
