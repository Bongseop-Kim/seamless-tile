"""Background primitive: a tile-filling rectangle in a resolved slot color."""

from dataclasses import dataclass

from app.engine.palette import Palette
from app.engine.units import fmt
from app.render.svg import escape_attr


@dataclass(frozen=True)
class Background:
    color_slot: str

    def render(self, tile_mm: float, palette: Palette, colorway_id: str | None = None) -> str:
        """An SVG fragment filling the tile. Composition assembles the document."""
        fill = escape_attr(palette.resolve_color(self.color_slot, colorway_id))
        side = fmt(tile_mm)
        return f'<rect x="0" y="0" width="{side}" height="{side}" fill="{fill}"/>'
