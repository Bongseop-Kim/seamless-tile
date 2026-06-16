from pydantic import Field

from app.api.schemas.common import BandPatternRequest


class CheckRequest(BandPatternRequest):
    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "widths_mm": [5, 5],
                    "colors": ["#1f3a5f"],
                    "tile_mm": 20,
                    "opacity": 0.5,
                }
            ]
        }
    }

    opacity: float = Field(0.5, gt=0, le=1)
