"""Generate API route for the current direct-intent engine path."""

from fastapi import APIRouter, HTTPException

from app.api.schemas.generate import GenerateRequest, GenerateResponse
from app.engine.generate import generate
from app.validate.intent import IntentInvalid

router = APIRouter(prefix="/generate", tags=["generate"])


@router.post("", response_model=GenerateResponse)
def generate_candidate(request: GenerateRequest) -> GenerateResponse:
    try:
        candidate = generate(
            request.intent,
            colorway_id=request.colorway_id,
            seed=request.seed,
        )
    except IntentInvalid as exc:
        raise HTTPException(status_code=422, detail=exc.errors) from None
    except (AssertionError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=[str(exc)]) from None

    return GenerateResponse(
        svg=candidate.svg,
        repro=vars(candidate.repro),
        warnings=candidate.warnings,
        layout_id=candidate.layout_id,
    )
