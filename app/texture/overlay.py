"""Compositing helpers: overlay a turbulence grain on top of the pattern using
feComponentTransfer (to set grain opacity) + feMerge (to stack over source)."""

from app.domain.units import fmt


def grain_primitives(freq: float, opacity: float, octaves: int = 2) -> str:
    return (
        f'<feTurbulence type="fractalNoise" baseFrequency="{fmt(freq)}" '
        f'numOctaves="{octaves}" stitchTiles="stitch" result="raw"/>'
        f'<feComponentTransfer in="raw" result="grain">'
        f'<feFuncA type="linear" slope="{fmt(opacity)}" intercept="0"/>'
        f"</feComponentTransfer>"
        f"<feMerge>"
        f'<feMergeNode in="SourceGraphic"/>'
        f'<feMergeNode in="grain"/>'
        f"</feMerge>"
    )
