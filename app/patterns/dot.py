"""Polka dot: one centred dot per spacing x spacing cell. The dot stays fully
inside its cell (radius <= spacing/2), so seamlessness comes entirely from the
repeat lattice; half_drop (default) breaks the grid look. Under half_drop the
compound tile's wrap stamps reconstruct any dot that lands on a tile edge."""

from app.domain.colorway import Colorway
from app.domain.pattern import Pattern
from app.domain.repeat import RepeatMode
from app.domain.units import fmt


class DotPattern(Pattern):
    repeat_default = RepeatMode.half_drop

    def __init__(
        self,
        spacing_mm: float,
        radius_mm: float,
        colorway: Colorway,
        repeat: RepeatMode | None = None,
    ):
        super().__init__(spacing_mm, colorway, repeat)
        self.spacing_mm = spacing_mm
        self.radius_mm = radius_mm

    def _bg_color(self) -> str:
        return self.colorway[1] if len(self.colorway) > 1 else "#ffffff"

    def motif(self) -> str:
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
