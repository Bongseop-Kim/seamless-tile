"""Weave: anisotropic turbulence displaces the pattern so threads appear to
cross. Asymmetric baseFrequency (warp vs weft) gives a directional weave."""

from app.domain.units import fmt
from app.texture.base import Texture


class WeaveTexture(Texture):
    def __init__(self, freq: float = 0.7, scale: float = 1.1, octaves: int = 2):
        self.freq = freq
        self.scale = scale
        self.octaves = octaves

    def primitives(self) -> str:
        fx = fmt(self.freq * 0.5)
        fy = fmt(self.freq)
        return (
            f'<feTurbulence type="turbulence" baseFrequency="{fx} {fy}" '
            f'numOctaves="{self.octaves}" stitchTiles="stitch" '
            f'result="noise"/>'
            f'<feDisplacementMap in="SourceGraphic" in2="noise" '
            f'scale="{fmt(self.scale)}" xChannelSelector="R" yChannelSelector="G"/>'
        )
