from pydantic import Field

from app.api.schemas.common import BandPatternRequest


class CheckRequest(BandPatternRequest):
    opacity: float = Field(0.5, gt=0, le=1)
