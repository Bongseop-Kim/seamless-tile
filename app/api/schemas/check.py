from pydantic import BaseModel, Field, field_validator, model_validator

from app.api.schemas.common import MAX_REPEATS, is_multiple, validate_colors


class CheckRequest(BaseModel):
    widths_mm: list[float] = Field(..., min_length=1)
    colors: list[str]
    tile_mm: float = Field(50.0, gt=0)
    opacity: float = Field(0.5, gt=0, le=1)
    texture: str | None = None

    _colors = field_validator("colors")(validate_colors)

    @field_validator("widths_mm")
    @classmethod
    def _positive_widths(cls, v: list[float]) -> list[float]:
        if any(w <= 0 for w in v):
            raise ValueError("widths_mm must all be positive")
        return v

    @model_validator(mode="after")
    def _commensurate(self) -> "CheckRequest":
        period = sum(self.widths_mm)
        if not is_multiple(self.tile_mm, period):
            raise ValueError(
                "tile_mm must be a positive integer multiple of sum(widths_mm)"
            )
        if self.tile_mm / period > MAX_REPEATS:
            raise ValueError("too many band repeats; increase widths or reduce tile_mm")
        return self
