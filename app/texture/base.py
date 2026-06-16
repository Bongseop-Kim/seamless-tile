"""Fabric texture as an SVG <filter>.

Textures stay vector/resolution-independent by expressing the weave as filter
primitives (feTurbulence, feDisplacementMap, ...) rather than baked bitmaps.
The filter region is pinned to the pattern tile in user space and turbulence
uses stitchTiles="stitch" so the textured tile remains a seamless repeat.
"""

from abc import ABC, abstractmethod

from app.domain.units import fmt


class Texture(ABC):
    @abstractmethod
    def primitives(self) -> str:
        """Filter primitive elements operating on SourceGraphic."""

    def to_filter_def(self, filter_id: str, tile_w: float, tile_h: float) -> str:
        return (
            f'<filter id="{filter_id}" x="0" y="0" '
            f'width="{fmt(tile_w)}" height="{fmt(tile_h)}" '
            f'filterUnits="userSpaceOnUse" primitiveUnits="userSpaceOnUse">'
            f"{self.primitives()}</filter>"
        )
