"""Engine input contract: the intent model.

Structural validation (types, ranges, unknown-field rejection) lives here via
pydantic. Cross-field semantic validation and repair live in ``app.validate.intent``.
"""

from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Canvas(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tile_mm: float = Field(gt=0)
    dpi: int = 300


class Production(BaseModel):
    model_config = ConfigDict(extra="forbid")

    method: Literal["digital", "screen"] = "digital"
    max_colors: int = Field(default=12, gt=0)


class ColorSlotSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    hex: str
    spot: str | None = None
    name: str | None = None


class PaletteSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slots: list[ColorSlotSpec] = Field(min_length=1)


class ColorwaySpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str | None = None
    mapping: dict[str, str]


class PathSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["straight", "wave", "custom"] = "straight"
    angle: float | None = None
    wavelength: float | None = Field(default=None, gt=0)
    amplitude: float | None = Field(default=None, ge=0)
    path_id: str | None = None


class Placement(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["lattice", "point_set", "path_following", "scatter"]
    host_layer: str | None = None
    lane: str | None = None
    path: PathSpec | None = None
    spacing_mm: float | None = Field(default=None, gt=0)
    phase_mm: float = 0.0
    rotation: Literal["follow_path", "fixed"] | None = None


# --- Layer params (type-specific) ---------------------------------------------


class BackgroundParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    color: str  # color slot id


class Band(BaseModel):
    model_config = ConfigDict(extra="forbid")

    offset_mm: float
    width_mm: float = Field(gt=0)
    color: str  # color slot id


class StripeParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    angle: float
    period_mm: float = Field(gt=0)
    bands: list[Band] = Field(min_length=1)


class MotifParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    motif_id: str
    size_mm: float = Field(gt=0)
    color: str | None = None  # single-color motif: one slot id
    colors: dict[str, str] | None = None  # multi-color motif: fill_slot -> palette_slot

    @model_validator(mode="after")
    def _exactly_one_color_spec(self) -> "MotifParams":
        if (self.color is not None) == bool(self.colors):
            raise ValueError(
                "motif params must set exactly one of `color` or non-empty `colors`"
            )
        return self


# --- Layers (discriminated on `type`) -----------------------------------------


class BackgroundLayer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    type: Literal["background"]
    params: BackgroundParams
    z_order: int
    opacity: float = Field(default=1.0, ge=0.0, le=1.0)
    clip: str | None = None


class StripeLayer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    type: Literal["stripe"]
    params: StripeParams
    z_order: int
    opacity: float = Field(default=1.0, ge=0.0, le=1.0)
    clip: str | None = None


class MotifLayer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    type: Literal["motif"]
    params: MotifParams
    placement: Placement | None = None
    z_order: int
    opacity: float = Field(default=1.0, ge=0.0, le=1.0)
    clip: str | None = None


Layer = Annotated[
    Union[BackgroundLayer, StripeLayer, MotifLayer],
    Field(discriminator="type"),
]


class Intent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent_version: int = 1
    canvas: Canvas
    seed: int = 0
    production: Production = Field(default_factory=Production)
    palette: PaletteSpec
    colorways: list[ColorwaySpec] = Field(min_length=1)
    layers: list[Layer] = Field(min_length=1)
