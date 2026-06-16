"""SVG <pattern> assembly: stamp a motif at placement offsets and wrap it."""

from app.domain.units import fmt


def build_pattern_def(
    pattern_id: str,
    motif_svg: str,
    tile_w: float,
    tile_h: float,
    placements,
    pattern_transform: str | None = None,
) -> str:
    groups = "".join(
        f'<g transform="translate({fmt(dx)},{fmt(dy)})">{motif_svg}</g>'
        for dx, dy in placements
    )
    transform_attr = (
        f' patternTransform="{pattern_transform}"' if pattern_transform else ""
    )
    return (
        f'<pattern id="{pattern_id}" patternUnits="userSpaceOnUse" '
        f'width="{fmt(tile_w)}" height="{fmt(tile_h)}"{transform_attr}>'
        f"{groups}</pattern>"
    )
