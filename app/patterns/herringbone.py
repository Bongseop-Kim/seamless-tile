"""Herringbone: vertical chevron (zigzag) strokes. Each stroke zig-zags with a
vertical period of `pitch`; strokes are spaced `pitch` apart horizontally. The
field is exactly periodic in both axes when tile_mm is a multiple of pitch, so
it tiles seamlessly under block repeat."""

from app.domain.colorway import Colorway
from app.domain.pattern import Pattern
from app.domain.repeat import RepeatMode
from app.domain.units import fmt


class HerringbonePattern(Pattern):
    repeat_default = RepeatMode.block

    def __init__(
        self,
        tile_mm: float,
        colorway: Colorway,
        stroke_mm: float,
        pitch_mm: float,
    ):
        super().__init__(tile_mm, colorway, RepeatMode.block)
        self.stroke_mm = stroke_mm
        self.pitch_mm = pitch_mm

    def _bg_color(self) -> str:
        return self.colorway[1] if len(self.colorway) > 1 else "#ffffff"

    def _zigzag(self, x0: float) -> str:
        p = self.pitch_mm
        amp = p / 2
        d = [f"M {fmt(x0)} 0"]
        y = 0.0
        while y < self.tile_mm - 1e-9:
            d.append(f"L {fmt(x0 + amp)} {fmt(y + p / 2)}")
            d.append(f"L {fmt(x0)} {fmt(y + p)}")
            y += p
        return " ".join(d)

    def motif(self) -> str:
        s = self.tile_mm
        parts = [
            f'<rect x="0" y="0" width="{fmt(s)}" height="{fmt(s)}" '
            f'fill="{self._bg_color()}"/>'
        ]
        x0 = 0.0
        col = 0
        while x0 < s - 1e-9:
            parts.append(
                f'<path d="{self._zigzag(x0)}" fill="none" '
                f'stroke="{self.colorway[col]}" '
                f'stroke-width="{fmt(self.stroke_mm)}"/>'
            )
            x0 += self.pitch_mm
            col += 1
        return "".join(parts)
