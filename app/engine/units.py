"""Unit conversion and SVG number formatting for engine geometry."""

DEFAULT_DPI = 300
MM_PER_INCH = 25.4


def mm_to_px(mm: float, dpi: int = DEFAULT_DPI) -> int:
    return round(mm / MM_PER_INCH * dpi)


def px_to_mm(px: float, dpi: int = DEFAULT_DPI) -> float:
    return px / dpi * MM_PER_INCH


def fmt(value: float) -> str:
    s = f"{float(value):.4f}".rstrip("0").rstrip(".")
    if s in ("", "-0", "-"):
        return "0"
    return s
