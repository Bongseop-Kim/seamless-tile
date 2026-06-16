"""Assemble a standalone, physical-mm SVG document around a pattern fill."""

from app.domain.pattern import Pattern
from app.domain.units import fmt

PREVIEW_REPEATS = 4
_PATTERN_ID = "tile"


def document_size_mm(pattern: Pattern, doc_mm: float | None = None) -> float:
    width, _ = document_dimensions_mm(pattern, doc_mm)
    return width


def document_dimensions_mm(
    pattern: Pattern, doc_mm: float | None = None
) -> tuple[float, float]:
    if doc_mm is not None:
        tile_w, tile_h = pattern.tile_size()
        if tile_w <= 0:
            return doc_mm, doc_mm
        return doc_mm, doc_mm * tile_h / tile_w
    cell_w, cell_h = pattern.base_size()
    return cell_w * PREVIEW_REPEATS, cell_h * PREVIEW_REPEATS


def render_document(pattern: Pattern, doc_mm: float | None = None) -> str:
    width, height = document_dimensions_mm(pattern, doc_mm)
    defs = pattern.to_pattern_def(_PATTERN_ID)
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" '
        f'width="{fmt(width)}mm" height="{fmt(height)}mm" '
        f'viewBox="0 0 {fmt(width)} {fmt(height)}">'
        f"<defs>{defs}</defs>"
        f'<rect width="{fmt(width)}" height="{fmt(height)}" fill="url(#{_PATTERN_ID})"/>'
        "</svg>"
    )
