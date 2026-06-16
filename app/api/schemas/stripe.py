from app.api.schemas.common import BandPatternRequest


class StripeRequest(BandPatternRequest):
    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "widths_mm": [10, 10],
                    "colors": ["#ffffff", "#1f3a5f"],
                    "tile_mm": 20,
                    "angle": 0,
                }
            ]
        }
    }

    angle: float = 0.0
