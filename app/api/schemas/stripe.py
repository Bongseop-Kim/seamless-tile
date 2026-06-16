from app.api.schemas.common import BandPatternRequest


class StripeRequest(BandPatternRequest):
    angle: float = 0.0
