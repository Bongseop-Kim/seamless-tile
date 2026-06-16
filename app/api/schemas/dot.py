from pydantic import BaseModel, Field, field_validator, model_validator

from app.api.schemas.common import RepeatMode, validate_colors, validate_texture


class DotRequest(BaseModel):
    radius_mm: float = Field(..., gt=0)
    spacing_mm: float = Field(..., gt=0)
    colors: list[str]
    repeat: RepeatMode = RepeatMode.half_drop
    texture: str | None = None

    _colors = field_validator("colors")(validate_colors)
    _texture = field_validator("texture")(validate_texture)

    @model_validator(mode="after")
    def _radius_fits(self) -> "DotRequest":
        if self.radius_mm > self.spacing_mm / 2:
            raise ValueError("radius_mm must be <= spacing_mm / 2")
        return self
