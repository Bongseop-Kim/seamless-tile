"""Engine input contract: the intent model.

Structural validation (types, ranges, unknown-field rejection) lives here via
pydantic. Cross-field semantic validation and repair live in ``app.validate.intent``.
"""

from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Canvas(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tile_mm: float = Field(gt=0)
    dpi: int = 300


class Production(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Top-level fabrication axis: yarn-dyed (woven) vs print. Color-count limits and
    # palette guidance differ by this. Legacy "digital"/"screen" (print sub-methods) map
    # to "print" for backward compat.
    method: Literal["yarn_dyed", "print"] = "print"
    max_colors: int = Field(default=12, gt=0)

    @field_validator("method", mode="before")
    @classmethod
    def _coerce_legacy_method(cls, v: object) -> object:
        return "print" if v in ("digital", "screen") else v


class ColorSlotSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    hex: str
    spot: str | None = None
    name: str | None = None


class PaletteSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slots: list[ColorSlotSpec] = Field(min_length=1, max_length=64)


class ColorwaySpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str | None = None
    mapping: dict[str, str]


class PathSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["straight", "wave"] = "straight"
    angle: float | None = None
    wavelength: float | None = Field(default=None, gt=0)
    amplitude: float | None = Field(default=None, ge=0)
    path_id: str | None = None


class LatticeSpec(BaseModel):
    """Two-basis-vector lattice. Absorbs the old block/half_drop/brick repeat modes.

    block: drop_fraction None. half_drop: drop_axis="column" (every column shifts down
    by drop_fraction*cell_h). brick: drop_axis="row" (every row shifts right by
    drop_fraction*cell_w). drop_fraction generalizes the discrete 1/2 to 1/2, 1/3, 1/4.
    """

    model_config = ConfigDict(extra="forbid")

    cell_w_mm: float = Field(gt=0)
    cell_h_mm: float = Field(gt=0)
    drop_fraction: float | None = Field(default=None, gt=0, lt=1)
    drop_axis: Literal["row", "column"] = "column"


class ScatterSpec(BaseModel):
    """Seed-deterministic torus scatter.

    ``poisson``: blue-noise dart-throwing with a torus minimum distance.
    ``sateen``: an N-end satin step grid that guarantees no two points share a row or
    column (deterministic, no RNG).
    """

    model_config = ConfigDict(extra="forbid")

    mode: Literal["poisson", "sateen"] = "poisson"
    min_dist_mm: float | None = Field(default=None, gt=0)
    count: int | None = Field(default=None, gt=0, le=10_000)
    sateen_n: int | None = Field(default=None, gt=1, le=1_024)
    sateen_step: int | None = Field(default=None, gt=0, le=1_024)


class PointSetSpec(BaseModel):
    """Explicit anchor points (mm) on the tile (e.g. lattice intersections)."""

    model_config = ConfigDict(extra="forbid")

    points: list[tuple[float, float]] = Field(min_length=1, max_length=10_000)


class Placement(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["lattice", "point_set", "path_following", "scatter"]
    host_layer: str | None = None
    lane: str | None = None
    path: PathSpec | None = None
    spacing_mm: float | None = Field(default=None, gt=0)
    phase_mm: float = 0.0
    rotation: Literal["follow_path", "fixed"] | None = None
    lattice: LatticeSpec | None = None
    scatter: ScatterSpec | None = None
    point_set: PointSetSpec | None = None

    @model_validator(mode="after")
    def _spec_matches_type(self) -> "Placement":
        specs = {
            "lattice": self.lattice,
            "scatter": self.scatter,
            "point_set": self.point_set,
        }
        expected = self.type if self.type in specs else None
        for name, spec in specs.items():
            if name == expected:
                if spec is None:
                    raise ValueError(f"{self.type} placement requires a `{name}` spec")
            elif spec is not None:
                if expected is None:
                    raise ValueError(f"{self.type} placement does not accept `{name}`")
                raise ValueError(
                    f"{self.type} placement does not accept `{name}`; use `{expected}`"
                )
        return self


# --- Layer params (type-specific) ---------------------------------------------


class BackgroundParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    color: str  # base ground color slot id (flat fill)


class Band(BaseModel):
    model_config = ConfigDict(extra="forbid")

    offset_mm: float
    width_mm: float = Field(gt=0)
    color: str  # color slot id


class StripeParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    angle: float
    period_mm: float = Field(gt=0)
    bands: list[Band] = Field(min_length=1, max_length=256)


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
    colorways: list[ColorwaySpec] = Field(min_length=1, max_length=32)
    layers: list[Layer] = Field(min_length=1, max_length=64)
