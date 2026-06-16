"""Shared request types and validators."""

from enum import Enum

from pydantic import BaseModel, Field

from app.domain.colorway import is_hex_color
from app.domain.repeat import RepeatMode

__all__ = ["RepeatMode", "ExportFormat", "ExportRequest", "validate_colors", "is_multiple"]

MAX_REPEATS = 200  # guard against absurd element counts (tiny pitch / large tile)


class ExportFormat(str, Enum):
    svg = "svg"
    png = "png"
    tiff = "tiff"


class ExportRequest(BaseModel):
    format: ExportFormat = ExportFormat.svg
    dpi: int = Field(300, ge=1, le=2400)
    width_mm: float | None = Field(None, gt=0)


def validate_colors(colors: list[str]) -> list[str]:
    if not colors:
        raise ValueError("at least one color is required")
    for c in colors:
        if not is_hex_color(c):
            raise ValueError(f"invalid hex color: {c!r}")
    return colors


def is_multiple(total: float, unit: float, tol: float = 1e-6) -> bool:
    if unit <= 0:
        return False
    ratio = total / unit
    return abs(ratio - round(ratio)) <= tol and round(ratio) >= 1
