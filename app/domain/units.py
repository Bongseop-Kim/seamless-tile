"""Unit conversion and SVG number formatting.

Textile geometry is kept in millimetres internally; conversion to pixels
happens only at the raster boundary (Phase 2). px = mm / 25.4 * dpi.
"""

DEFAULT_DPI = 300
MM_PER_INCH = 25.4


def mm_to_px(mm: float, dpi: int = DEFAULT_DPI) -> int:
    return round(mm / MM_PER_INCH * dpi)


def px_to_mm(px: float, dpi: int = DEFAULT_DPI) -> float:
    return px / dpi * MM_PER_INCH


def fmt(value: float) -> str:
    """Compact SVG number: trim trailing zeros, avoid '-0'."""
    s = f"{float(value):.4f}".rstrip("0").rstrip(".")
    if s in ("", "-0", "-"):
        return "0"
    return s
