"""Assemble a standalone, physical-mm SVG document around a pattern fill."""

from app.domain.pattern import Pattern
from app.domain.units import fmt

PREVIEW_REPEATS = 4
_PATTERN_ID = "tile"


def document_size_mm(pattern: Pattern, doc_mm: float | None = None) -> float:
    if doc_mm is not None:
        return doc_mm
    cell_w, cell_h = pattern.base_size()
    return max(cell_w, cell_h) * PREVIEW_REPEATS


def render_document(pattern: Pattern, doc_mm: float | None = None) -> str:
    size = document_size_mm(pattern, doc_mm)
    defs = pattern.texture_filter_def(_PATTERN_ID) + pattern.to_pattern_def(_PATTERN_ID)
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" '
        f'width="{fmt(size)}mm" height="{fmt(size)}mm" '
        f'viewBox="0 0 {fmt(size)} {fmt(size)}">'
        f"<defs>{defs}</defs>"
        f'<rect width="{fmt(size)}" height="{fmt(size)}" fill="url(#{_PATTERN_ID})"/>'
        "</svg>"
    )
