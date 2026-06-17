"""Assemble standalone physical-mm SVG documents from composed tile content."""

from app.engine.units import fmt


def render_svg_document(
    body: str,
    width_mm: float,
    height_mm: float | None = None,
    defs: str = "",
) -> str:
    height = height_mm if height_mm is not None else width_mm
    defs_block = f"<defs>{defs}</defs>" if defs else ""
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" '
        f'width="{fmt(width_mm)}mm" height="{fmt(height)}mm" '
        f'viewBox="0 0 {fmt(width_mm)} {fmt(height)}">'
        f"{defs_block}{body}"
        "</svg>"
    )
