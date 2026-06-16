from enum import Enum

from pydantic import BaseModel, Field, field_validator, model_validator

from app.api.schemas.common import (
    MAX_REPEATS,
    is_multiple,
    validate_colors,
    validate_texture,
)


class LinePosition(str, Enum):
    start = "start"
    end = "end"
    center = "center"


class LineStyle(str, Enum):
    solid = "solid"
    dotted = "dotted"


class DotShape(str, Enum):
    rect = "rect"
    circle = "circle"


class DotRepeat(str, Enum):
    block = "block"
    half_drop = "half_drop"
    brick = "brick"


def _validate_color(value: str) -> str:
    validate_colors([value])
    return value


class StripeLine(BaseModel):
    position: LinePosition
    width_mm: float = Field(..., gt=0)
    color: str
    offset_mm: float = 0.0
    style: LineStyle = LineStyle.solid
    dot_length_mm: float | None = Field(None, gt=0)
    gap_mm: float | None = Field(None, gt=0)
    dot_shape: DotShape = DotShape.rect

    _color = field_validator("color")(_validate_color)

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

    _color = field_validator("color")(_validate_color)


class DotLayer(BaseModel):
    radius_mm: float = Field(..., gt=0)
    color: str
    spacing_x_mm: float = Field(..., gt=0)
    spacing_y_mm: float = Field(..., gt=0)
    offset_x_mm: float = 0.0
    offset_y_mm: float = 0.0
    repeat: DotRepeat = DotRepeat.block

    _color = field_validator("color")(_validate_color)

    @model_validator(mode="after")
    def _radius_fits_spacing(self) -> "DotLayer":
        if self.radius_mm > min(self.spacing_x_mm, self.spacing_y_mm) / 2:
            raise ValueError("radius_mm must be <= half of dot spacing")
        return self


class StripeDotRequest(BaseModel):
    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "tile_mm": 48,
                    "angle": -32,
                    "background_color": "#10243a",
                    "stripes": [
                        {
                            "offset_mm": 8,
                            "width_mm": 14,
                            "color": "#0a1a2b",
                            "edge_lines": [
                                {
                                    "position": "start",
                                    "width_mm": 0.7,
                                    "color": "#e02b22",
                                    "style": "dotted",
                                    "dot_length_mm": 1.2,
                                    "gap_mm": 1.2,
                                }
                            ],
                        }
                    ],
                    "dot_layers": [
                        {
                            "radius_mm": 0.5,
                            "color": "#33506c",
                            "spacing_x_mm": 8,
                            "spacing_y_mm": 8,
                        }
                    ],
                }
            ]
        }
    }

    tile_mm: float = Field(..., gt=0)
    angle: float = 0.0
    background_color: str
    texture: str | None = None
    stripes: list[StripeBand] = Field(..., min_length=1)
    dot_layers: list[DotLayer] = Field(default_factory=list)

    _background_color = field_validator("background_color")(_validate_color)
    _texture = field_validator("texture")(validate_texture)

    @model_validator(mode="after")
    def _fits_tile_and_repeat_guard(self) -> "StripeDotRequest":
        for stripe in self.stripes:
            if stripe.offset_mm + stripe.width_mm > self.tile_mm:
                raise ValueError("stripe offset_mm + width_mm must be <= tile_mm")
            for line in stripe.edge_lines:
                if line.style == LineStyle.dotted:
                    assert line.dot_length_mm is not None and line.gap_mm is not None
                    pitch = line.dot_length_mm + line.gap_mm
                    if not is_multiple(self.tile_mm, pitch):
                        raise ValueError(
                            "tile_mm must be a positive integer multiple of dotted line pitch"
                        )
                    count = self.tile_mm / pitch
                    if count > MAX_REPEATS:
                        raise ValueError("too many line dots; increase dot pitch")
        for layer in self.dot_layers:
            if not is_multiple(self.tile_mm, layer.spacing_x_mm) or not is_multiple(
                self.tile_mm, layer.spacing_y_mm
            ):
                raise ValueError(
                    "tile_mm must be a positive integer multiple of dot spacing"
                )
            count = (self.tile_mm / layer.spacing_x_mm) * (
                self.tile_mm / layer.spacing_y_mm
            )
            if count > MAX_REPEATS:
                raise ValueError("too many dots; increase dot spacing")
        return self
