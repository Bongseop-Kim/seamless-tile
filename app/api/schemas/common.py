"""Shared request types and validators."""

from enum import Enum

from pydantic import BaseModel, Field, field_validator, model_validator

from app.domain.colorway import is_hex_color
from app.domain.repeat import RepeatMode

__all__ = [
    "RepeatMode",
    "ExportFormat",
    "BandPatternRequest",
    "LinePosition",
    "LineStyle",
    "StripeLineDotShape",
    "StripeLine",
    "StripeBand",
    "validate_colors",
    "validate_color",
    "is_multiple",
]

MAX_REPEATS = 200  # guard against absurd element counts (tiny pitch / large tile)


class ExportFormat(str, Enum):
    svg = "svg"
    png = "png"
    tiff = "tiff"


class LinePosition(str, Enum):
    start = "start"
    end = "end"
    center = "center"


class LineStyle(str, Enum):
    solid = "solid"
    dotted = "dotted"


class StripeLineDotShape(str, Enum):
    rect = "rect"
    circle = "circle"


def validate_colors(colors: list[str]) -> list[str]:
    if not colors:
        raise ValueError("at least one color is required")
    for c in colors:
        if not is_hex_color(c):
            raise ValueError(f"invalid hex color: {c!r}")
    return colors


def validate_color(value: str) -> str:
    validate_colors([value])
    return value


def is_multiple(total: float, unit: float, tol: float = 1e-6) -> bool:
    if unit <= 0:
        return False
    ratio = total / unit
    return abs(ratio - round(ratio)) <= tol and round(ratio) >= 1


class BandPatternRequest(BaseModel):
    """Base for band-based patterns (stripe, gingham): equal-width colour bands
    that tile seamlessly when tile_mm is an integer multiple of the band period."""

    model_config = {"extra": "forbid"}

    widths_mm: list[float] = Field(..., min_length=1)
    colors: list[str]
    tile_mm: float = Field(50.0, gt=0)

    _colors = field_validator("colors")(validate_colors)

    @field_validator("widths_mm")
    @classmethod
    def _positive_widths(cls, v: list[float]) -> list[float]:
        if any(w <= 0 for w in v):
            raise ValueError("widths_mm must all be positive")
        return v

    @model_validator(mode="after")
    def _commensurate(self) -> "BandPatternRequest":
        period = sum(self.widths_mm)
        if not is_multiple(self.tile_mm, period):
            raise ValueError(
                "tile_mm must be a positive integer multiple of sum(widths_mm)"
            )
        if self.tile_mm / period > MAX_REPEATS:
            raise ValueError("too many band repeats; increase widths or reduce tile_mm")
        return self


class StripeLine(BaseModel):
    position: LinePosition
    width_mm: float = Field(..., gt=0)
    color: str
    offset_mm: float = 0.0
    style: LineStyle = LineStyle.solid
    dot_length_mm: float | None = Field(None, gt=0)
    gap_mm: float | None = Field(None, gt=0)
    dot_shape: StripeLineDotShape = StripeLineDotShape.rect

    _color = field_validator("color")(validate_color)

    @model_validator(mode="after")
    def _dotted_has_pitch(self) -> "StripeLine":
        if self.style == LineStyle.dotted:
            if self.dot_length_mm is None or self.gap_mm is None:
                raise ValueError("dotted lines require dot_length_mm and gap_mm")
        return self


class StripeBand(BaseModel):
    offset_mm: float = Field(..., ge=0)
    width_mm: float = Field(..., gt=0)
    color: str
    opacity: float = Field(1.0, gt=0, le=1)
    edge_lines: list[StripeLine] = Field(default_factory=list)

    _color = field_validator("color")(validate_color)
