from pydantic import BaseModel, Field, field_validator, model_validator

from app.api.schemas.common import (
    MAX_REPEATS,
    LineStyle,
    StripeBand,
    is_multiple,
    validate_color,
    validate_colors,
)

AXIS_ALIGNED_ANGLE_TOLERANCE = 1e-6


def _is_axis_aligned(angle: float) -> bool:
    normalized = angle % 90
    return (
        abs(normalized) <= AXIS_ALIGNED_ANGLE_TOLERANCE
        or abs(normalized - 90) <= AXIS_ALIGNED_ANGLE_TOLERANCE
    )


class StripeRequest(BaseModel):
    model_config = {
        "extra": "forbid",
        "json_schema_extra": {
            "examples": [
                {
                    "widths_mm": [10, 10],
                    "colors": ["#ffffff", "#1f3a5f"],
                    "tile_mm": 20,
                    "angle": -45,
                },
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
                                },
                                {
                                    "position": "end",
                                    "width_mm": 0.4,
                                    "color": "#f0f2ee",
                                },
                            ],
                        }
                    ],
                }
            ]
        }
    }

    widths_mm: list[float] | None = None
    colors: list[str] | None = None
    tile_mm: float = Field(50.0, gt=0)
    angle: float = -45.0
    background_color: str = "#ffffff"
    stripes: list[StripeBand] = Field(default_factory=list)

    _background_color = field_validator("background_color")(validate_color)

    @field_validator("colors")
    @classmethod
    def _colors_valid(cls, v: list[str] | None) -> list[str] | None:
        if v is not None:
            validate_colors(v)
        return v

    @field_validator("widths_mm")
    @classmethod
    def _positive_widths(cls, v: list[float] | None) -> list[float] | None:
        if v is not None and any(w <= 0 for w in v):
            raise ValueError("widths_mm must all be positive")
        return v

    def _has_legacy_fields(self) -> bool:
        return self.widths_mm is not None or self.colors is not None

    @model_validator(mode="after")
    def _validate_legacy_or_composed(self) -> "StripeRequest":
        if _is_axis_aligned(self.angle):
            raise ValueError("stripe angle must be diagonal, not axis-aligned")

        if self.stripes:
            if self._has_legacy_fields():
                raise ValueError("use either stripes or widths_mm/colors, not both")
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
                        if self.tile_mm / pitch > MAX_REPEATS:
                            raise ValueError("too many line dots; increase dot pitch")
            return self

        if self.widths_mm is None or self.colors is None:
            raise ValueError("widths_mm and colors are required unless stripes is provided")
        period = sum(self.widths_mm)
        if not is_multiple(self.tile_mm, period):
            raise ValueError(
                "tile_mm must be a positive integer multiple of sum(widths_mm)"
            )
        if self.tile_mm / period > MAX_REPEATS:
            raise ValueError("too many band repeats; increase widths or reduce tile_mm")
        return self
