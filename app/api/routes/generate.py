"""Product generate route: a thin adapter over the engine candidate orchestrator.

Session 6 is intent-direct (the stub builder passes a supplied ``intent`` through);
the LLM/image builder is session 7. Diversification, ranking and de-dup live in
``app.engine.candidates`` so the determinism contract stays in the engine layer.
"""

from __future__ import annotations

import time
from dataclasses import asdict

from fastapi import APIRouter, HTTPException

from app.api.schemas.generate import (
    CandidateResponse,
    GenerateRequest,
    GenerateResponse,
)
from app.core.observability import get_request_id, log_metrics
from app.engine.candidates import generate_candidates
from app.validate.intent import IntentInvalid

router = APIRouter(prefix="/generate", tags=["generate"])

# Fields accepted on the product surface but not wired to behavior until session 7.
_DEFERRED_FIELDS = ("prompt", "reference_image", "canvas", "palette")


@router.post("", response_model=GenerateResponse)
def generate_candidate(request: GenerateRequest) -> GenerateResponse:
    builder_warnings = [
        f"{name} is accepted but not used until the session 7 adapter; ignored"
        for name in _DEFERRED_FIELDS
        if getattr(request, name) is not None
    ]

    # Stub builder: intent-direct only this session.
    if request.intent is None:
        raise HTTPException(
            status_code=422,
            detail=[
                "prompt->intent requires the LLM adapter (session 7); "
                "supply `intent` directly for now"
            ],
        )

    started = time.perf_counter()
    try:
        result = generate_candidates(
            request.intent,
            candidate_count=request.candidate_count,
            seed=request.seed,
            colorway=request.colorway,
        )
    except IntentInvalid as exc:
        raise HTTPException(status_code=422, detail=exc.errors) from None
    except (AssertionError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=[str(exc)]) from None
    generate_ms = round((time.perf_counter() - started) * 1000, 1)

    if not result.candidates:
        raise HTTPException(
            status_code=500, detail=["no candidate could be composed"]
        )

    candidates = [
        CandidateResponse(
            id=rc.id,
            svg=rc.candidate.svg,
            intent=rc.intent.model_dump(mode="json"),
            layout_id=rc.candidate.layout_id,
            source_fidelity=rc.source_fidelity,
            repro=asdict(rc.candidate.repro),
        )
        for rc in result.candidates
    ]
    warnings = builder_warnings + result.warnings

    distinct_layouts = len({rc.candidate.layout_id for rc in result.candidates})
    log_metrics(
        "generate",
        requested=request.candidate_count,
        returned=len(candidates),
        distinct_layouts=distinct_layouts,
        available_strategies=result.available_strategy_count,
        warnings=len(warnings),
        generate_ms=generate_ms,
    )

    return GenerateResponse(
        request_id=get_request_id(),
        candidates=candidates,
        warnings=warnings,
    )
