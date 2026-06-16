"""Shared request types and validators."""

from enum import Enum

from pydantic import BaseModel, Field, field_validator, model_validator

from app.domain.colorway import is_hex_color
from app.domain.repeat import RepeatMode
from app.texture import KNOWN_TEXTURES

__all__ = [
    "RepeatMode",
    "ExportFormat",
    "BandPatternRequest",
    "validate_colors",
    "validate_texture",
    "is_multiple",
]

MAX_REPEATS = 200  # guard against absurd element counts (tiny pitch / large tile)


class ExportFormat(str, Enum):
    svg = "svg"
    png = "png"
    tiff = "tiff"


def validate_colors(colors: list[str]) -> list[str]:
    if not colors:
        raise ValueError("at least one color is required")
    for c in colors:
        if not is_hex_color(c):
            raise ValueError(f"invalid hex color: {c!r}")
    return colors


def validate_texture(value: str | None) -> str | None:
    if value is not None and value not in KNOWN_TEXTURES:
        raise ValueError(
            f"unknown texture: {value!r}; choose one of {sorted(KNOWN_TEXTURES)}"
        )
    return value


def is_multiple(total: float, unit: float, tol: float = 1e-6) -> bool:
    if unit <= 0:
        return False
    ratio = total / unit
    return abs(ratio - round(ratio)) <= tol and round(ratio) >= 1


class BandPatternRequest(BaseModel):
    """Base for band-based patterns (stripe, gingham): equal-width colour bands
    that tile seamlessly when tile_mm is an integer multiple of the band period."""

    widths_mm: list[float] = Field(..., min_length=1)
    colors: list[str]
    tile_mm: float = Field(50.0, gt=0)
    texture: str | None = None

    _colors = field_validator("colors")(validate_colors)
    _texture = field_validator("texture")(validate_texture)

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
