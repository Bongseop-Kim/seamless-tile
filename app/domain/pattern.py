"""Pattern abstraction: a motif + a repeat mode -> a seamless <pattern> def."""

from abc import ABC, abstractmethod
from app.domain.colorway import Colorway
from app.domain.repeat import RepeatMode, placements
from app.domain.tile import build_pattern_def


class Pattern(ABC):
    repeat_default = RepeatMode.block

    def __init__(self, tile_mm: float, colorway: Colorway, repeat: RepeatMode | None = None):
        self.tile_mm = tile_mm
        self.colorway = colorway
        self.repeat = repeat or self.repeat_default

    @abstractmethod
    def motif(self) -> str:
        """SVG content of a single seamless base cell."""

    def base_size(self) -> tuple[float, float]:
        """Size (mm) of the seamless base cell. Override when not square."""
        return (self.tile_mm, self.tile_mm)

    def pattern_transform(self) -> str | None:
        """Optional SVG patternTransform (e.g. rotate); seamless is preserved."""
        return None

    def tile_size(self) -> tuple[float, float]:
        cell_w, cell_h = self.base_size()
        tile_w, tile_h, _ = placements(cell_w, cell_h, self.repeat)
        return tile_w, tile_h

    def to_pattern_def(self, pattern_id: str) -> str:
        cell_w, cell_h = self.base_size()
        tile_w, tile_h, places = placements(cell_w, cell_h, self.repeat)
        return build_pattern_def(
            pattern_id,
            self.motif(),
            tile_w,
            tile_h,
            places,
            self.pattern_transform(),
        )
