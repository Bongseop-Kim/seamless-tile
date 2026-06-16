from pydantic import BaseModel, Field, field_validator, model_validator

from app.api.schemas.common import RepeatMode, validate_colors


class DotRequest(BaseModel):
    radius_mm: float = Field(..., gt=0)
    spacing_mm: float = Field(..., gt=0)
    colors: list[str]
    repeat: RepeatMode = RepeatMode.half_drop
    texture: str | None = None

    _colors = field_validator("colors")(validate_colors)

    @model_validator(mode="after")
    def _radius_fits(self) -> "DotRequest":
        if self.radius_mm > self.spacing_mm / 2:
            raise ValueError("radius_mm must be <= spacing_mm / 2")
        return self
