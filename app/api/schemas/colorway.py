from pydantic import BaseModel, model_validator

from app.api.schemas.common import validate_colors
from app.domain.colorway import PALETTES


class ColorwayRequest(BaseModel):
    """Recolor an existing pattern. Provide exactly one of `colors` or `palette`."""

    colors: list[str] | None = None
    palette: str | None = None

    @model_validator(mode="after")
    def _exactly_one(self) -> "ColorwayRequest":
        if bool(self.colors) == bool(self.palette):
            raise ValueError("provide exactly one of 'colors' or 'palette'")
        if self.colors is not None:
            validate_colors(self.colors)
        if self.palette is not None and self.palette not in PALETTES:
            raise ValueError(
                f"unknown palette: {self.palette!r}; choose one of {sorted(PALETTES)}"
            )
        return self
