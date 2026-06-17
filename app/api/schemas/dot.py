from enum import Enum

from pydantic import BaseModel, Field, field_validator, model_validator

from app.api.schemas.common import (
    MAX_REPEATS,
    RepeatMode,
    is_multiple,
    validate_color,
    validate_colors,
)


class DotShape(str, Enum):
    circle = "circle"
    square = "square"
    diamond = "diamond"
    teardrop = "teardrop"


class DotLayer(BaseModel):
    shape: DotShape = DotShape.circle
    size_mm: float = Field(..., gt=0)
    color: str
    spacing_x_mm: float = Field(..., gt=0)
    spacing_y_mm: float = Field(..., gt=0)
    offset_x_mm: float = 0.0
    offset_y_mm: float = 0.0
    repeat: RepeatMode = RepeatMode.block

    _color = field_validator("color")(validate_color)

    @model_validator(mode="after")
    def _size_fits_spacing(self) -> "DotLayer":
        if self.size_mm > min(self.spacing_x_mm, self.spacing_y_mm):
            raise ValueError("size_mm must be <= the smaller dot spacing")
        return self


class DotRequest(BaseModel):
    model_config = {
        "extra": "forbid",
        "json_schema_extra": {
            "examples": [
                {
                    "radius_mm": 3,
                    "spacing_mm": 12,
                    "colors": ["#1a1a1a", "#ffffff"],
                    "repeat": "half_drop",
                },
                {
                    "tile_mm": 48,
                    "background_color": "#f7f3eb",
                    "layers": [
                        {
                            "shape": "circle",
                            "size_mm": 4,
                            "color": "#16233f",
                            "spacing_x_mm": 12,
                            "spacing_y_mm": 12,
                            "repeat": "half_drop",
                        },
                        {
                            "shape": "diamond",
                            "size_mm": 2,
                            "color": "#b23a48",
                            "spacing_x_mm": 24,
                            "spacing_y_mm": 24,
                            "offset_x_mm": 6,
                            "offset_y_mm": 6,
                        },
                    ],
                }
            ]
        }
    }

    radius_mm: float | None = Field(None, gt=0)
    spacing_mm: float | None = Field(None, gt=0)
    colors: list[str] | None = None
    repeat: RepeatMode = RepeatMode.half_drop
    tile_mm: float | None = Field(None, gt=0)
    background_color: str = "#ffffff"
    layers: list[DotLayer] = Field(default_factory=list)

    _background_color = field_validator("background_color")(validate_color)

    @field_validator("colors")
    @classmethod
    def _colors_valid(cls, v: list[str] | None) -> list[str] | None:
        if v is not None:
            validate_colors(v)
        return v

    def _has_legacy_fields(self) -> bool:
        return (
            self.radius_mm is not None
            or self.spacing_mm is not None
            or self.colors is not None
        )

    @model_validator(mode="after")
    def _validate_legacy_or_layers(self) -> "DotRequest":
        if self.layers:
            if self._has_legacy_fields():
                raise ValueError("use either layers or radius_mm/spacing_mm/colors, not both")
            if self.tile_mm is None:
                raise ValueError("tile_mm is required when layers are provided")
            for layer in self.layers:
                if not is_multiple(self.tile_mm, layer.spacing_x_mm) or not is_multiple(
                    self.tile_mm, layer.spacing_y_mm
                ):
                    raise ValueError(
                        "tile_mm must be a positive integer multiple of layer spacing"
                    )
                count = (self.tile_mm / layer.spacing_x_mm) * (
                    self.tile_mm / layer.spacing_y_mm
                )
                if count > MAX_REPEATS:
                    raise ValueError("too many dots; increase dot spacing")
            return self

        if self.radius_mm is None or self.spacing_mm is None or self.colors is None:
            raise ValueError(
                "radius_mm, spacing_mm, and colors are required unless layers is provided"
            )
        if self.radius_mm > self.spacing_mm / 2:
            raise ValueError("radius_mm must be <= spacing_mm / 2")
        return self
