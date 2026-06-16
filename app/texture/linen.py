"""Linen: soft, low-frequency fractal displacement for a coarse natural-fibre
look. Gentler than weave (lower freq, smaller scale)."""

from app.domain.units import fmt
from app.texture.base import Texture


class LinenTexture(Texture):
    def __init__(self, freq: float = 0.35, scale: float = 0.8, octaves: int = 3):
        self.freq = freq
        self.scale = scale
        self.octaves = octaves

    def primitives(self) -> str:
        return (
            f'<feTurbulence type="fractalNoise" '
            f'baseFrequency="{fmt(self.freq)}" numOctaves="{self.octaves}" '
            f'stitchTiles="stitch" result="noise"/>'
            f'<feDisplacementMap in="SourceGraphic" in2="noise" '
            f'scale="{fmt(self.scale)}" xChannelSelector="R" yChannelSelector="G"/>'
        )
