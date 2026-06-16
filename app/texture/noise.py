"""Noise: a fine translucent grain overlaid on the pattern (yarn irregularity)
without displacing the geometry."""

from app.texture.base import Texture
from app.texture.overlay import grain_primitives


class NoiseTexture(Texture):
    def __init__(self, freq: float = 1.1, opacity: float = 0.18, octaves: int = 2):
        self.freq = freq
        self.opacity = opacity
        self.octaves = octaves

    def primitives(self) -> str:
        return grain_primitives(self.freq, self.opacity, self.octaves)
