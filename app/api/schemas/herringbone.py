from pydantic import BaseModel, Field, field_validator, model_validator

from app.api.schemas.common import (
    MAX_REPEATS,
    is_multiple,
    validate_colors,
)


class HerringboneRequest(BaseModel):
    model_config = {
        "extra": "forbid",
        "json_schema_extra": {
            "examples": [
                {
                    "stroke_mm": 2,
                    "pitch_mm": 10,
                    "colors": ["#3e2f1c"],
                    "tile_mm": 40,
                }
            ]
        }
    }

    stroke_mm: float = Field(..., gt=0)
    pitch_mm: float = Field(..., gt=0)
    colors: list[str]
    tile_mm: float = Field(50.0, gt=0)

    _colors = field_validator("colors")(validate_colors)

    @model_validator(mode="after")
    def _commensurate(self) -> "HerringboneRequest":
        if self.stroke_mm > self.pitch_mm:
            raise ValueError("stroke_mm must be <= pitch_mm")
        if not is_multiple(self.tile_mm, self.pitch_mm):
            raise ValueError("tile_mm must be a positive integer multiple of pitch_mm")
        if self.tile_mm / self.pitch_mm > MAX_REPEATS:
            raise ValueError("too many stroke repeats; increase pitch_mm or reduce tile_mm")
        return self
