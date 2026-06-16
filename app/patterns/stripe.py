"""Stripe: full-bleed colour bands. Seamless when tile_mm is a multiple of the
band period; arbitrary angles stay seamless via patternTransform rotate()."""

from app.domain.colorway import Colorway
from app.domain.pattern import Pattern
from app.domain.repeat import RepeatMode
from app.domain.units import fmt


class StripePattern(Pattern):
    repeat_default = RepeatMode.block

    def __init__(self, tile_mm: float, colorway: Colorway, widths_mm, angle: float = 0.0):
        super().__init__(tile_mm, colorway, RepeatMode.block)
        self.widths_mm = list(widths_mm)
        self.angle = angle

    def pattern_transform(self) -> str | None:
        return f"rotate({fmt(self.angle)})" if self.angle else None

    def motif(self) -> str:
        parts = []
        x = 0.0
        i = 0
        while x < self.tile_mm - 1e-9:
            w = self.widths_mm[i % len(self.widths_mm)]
            parts.append(
                f'<rect x="{fmt(x)}" y="0" width="{fmt(w)}" '
                f'height="{fmt(self.tile_mm)}" fill="{self.colorway[i]}"/>'
            )
            x += w
            i += 1
        return "".join(parts)
