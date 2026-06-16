"""Check / gingham: translucent vertical + horizontal bands over a white
ground. Overlaps composite (alpha) into the darker intersection. Seamless when
tile_mm is a multiple of the band period."""

from app.domain.colorway import Colorway
from app.domain.pattern import Pattern
from app.domain.repeat import RepeatMode
from app.domain.units import fmt


class CheckPattern(Pattern):
    repeat_default = RepeatMode.block

    def __init__(
        self,
        tile_mm: float,
        colorway: Colorway,
        widths_mm,
        opacity: float = 0.5,
    ):
        super().__init__(tile_mm, colorway, RepeatMode.block)
        self.widths_mm = list(widths_mm)
        self.opacity = opacity

    def _bands(self, horizontal: bool) -> str:
        parts = []
        pos = 0.0
        i = 0
        while pos < self.tile_mm - 1e-9:
            w = self.widths_mm[i % len(self.widths_mm)]
            color = self.colorway[i]
            if horizontal:
                rect = (
                    f'<rect x="0" y="{fmt(pos)}" width="{fmt(self.tile_mm)}" '
                    f'height="{fmt(w)}"'
                )
            else:
                rect = (
                    f'<rect x="{fmt(pos)}" y="0" width="{fmt(w)}" '
                    f'height="{fmt(self.tile_mm)}"'
                )
            parts.append(
                f'{rect} fill="{color}" fill-opacity="{fmt(self.opacity)}"/>'
            )
            pos += w
            i += 1
        return "".join(parts)

    def motif(self) -> str:
        bg = (
            f'<rect x="0" y="0" width="{fmt(self.tile_mm)}" '
            f'height="{fmt(self.tile_mm)}" fill="#ffffff"/>'
        )
        return bg + self._bands(horizontal=False) + self._bands(horizontal=True)
