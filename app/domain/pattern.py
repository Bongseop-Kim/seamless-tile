"""Pattern abstraction: a motif + a repeat mode -> a seamless <pattern> def."""

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from app.domain.colorway import Colorway
from app.domain.repeat import RepeatMode, placements
from app.domain.tile import build_pattern_def

if TYPE_CHECKING:
    from app.texture.base import Texture


class Pattern(ABC):
    repeat_default = RepeatMode.block

    def __init__(self, tile_mm: float, colorway: Colorway, repeat: RepeatMode | None = None):
        self.tile_mm = tile_mm
        self.colorway = colorway
        self.repeat = repeat or self.repeat_default
        self.texture: "Texture | None" = None

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

    def _texture_filter_id(self, pattern_id: str) -> str:
        return f"{pattern_id}-tex"

    def texture_filter_def(self, pattern_id: str) -> str:
        if self.texture is None:
            return ""
        tile_w, tile_h = self.tile_size()
        return self.texture.to_filter_def(
            self._texture_filter_id(pattern_id), tile_w, tile_h
        )

    def to_pattern_def(self, pattern_id: str) -> str:
        cell_w, cell_h = self.base_size()
        tile_w, tile_h, places = placements(cell_w, cell_h, self.repeat)
        group_filter = (
            self._texture_filter_id(pattern_id) if self.texture is not None else None
        )
        return build_pattern_def(
            pattern_id,
            self.motif(),
            tile_w,
            tile_h,
            places,
            self.pattern_transform(),
            group_filter,
        )
