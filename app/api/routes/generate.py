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
from app.adapters.base import AdapterClientError
from app.adapters.embedding import get_default_embedding_client
from app.adapters.image import build_intent as image_build_intent
from app.adapters.llm import build_intent as llm_build_intent
from app.adapters.motif_resolver import resolve_motifs
from app.core.observability import get_request_id, log_metrics
from app.engine.candidates import SOURCE_FIDELITY_VECTOR, generate_candidates
from app.motifs.store import get_default_store
from app.validate.intent import IntentInvalid

router = APIRouter(prefix="/generate", tags=["generate"])

# Product-surface fields that are ignored when a raw `intent` is supplied directly
# (the intent is then authoritative); they ARE honored on the prompt/image paths.
_INTENT_DIRECT_IGNORED = ("prompt", "reference_image", "canvas", "palette")


def _run_adapter(call):
    """Invoke an adapter, mapping its failures to HTTP status: IntentInvalid -> 422
    (semantic, after the adapter's own one re-prompt), client/decoding failure -> 502."""
    try:
        return call()
    except IntentInvalid as exc:
        raise HTTPException(status_code=422, detail=exc.errors) from None
    except AdapterClientError as exc:
        raise HTTPException(status_code=502, detail=[str(exc)]) from None


@router.post("", response_model=GenerateResponse)
async def generate_candidate(request: GenerateRequest) -> GenerateResponse:
    # Resolve the intent: direct > reference_image > prompt. The adapters live outside
    # the engine; they freeze/cache their (non-deterministic) output so the pipeline
    # below stays deterministic. Adapter IntentInvalid -> 422 (after its own one-shot
    # re-prompt); a missing/failed external client -> 5xx.
    warnings: list[str] = []
    source_fidelity = SOURCE_FIDELITY_VECTOR
    motif_specs: list[dict] = []

    if request.intent is not None:
        intent_raw = request.intent
        warnings += [
            f"{name} ignored because `intent` was supplied directly"
            for name in _INTENT_DIRECT_IGNORED
            if getattr(request, name) is not None
        ]
    elif request.reference_image is not None:
        adapted = _run_adapter(
            lambda: image_build_intent(request.reference_image, canvas=request.canvas)
        )
        intent_raw, source_fidelity = adapted.intent, adapted.source_fidelity
        warnings += adapted.warnings
    elif request.prompt is not None:
        adapted = _run_adapter(
            lambda: llm_build_intent(
                request.prompt, canvas=request.canvas, palette=request.palette
            )
        )
        intent_raw, source_fidelity = adapted.intent, adapted.source_fidelity
        warnings += adapted.warnings
        motif_specs = adapted.motif_specs
    else:
        raise HTTPException(
            status_code=422,
            detail=["one of `intent`, `reference_image`, or `prompt` is required"],
        )

    # S10 glue: resolve each motif spec to a concrete motif_id and inject it into the
    # intent BEFORE the engine sees it (the engine only composes concrete motif ids).
    # Selection is deterministic; miss-path generation is frozen by the adapter cache.
    # IntentInvalid -> 422, generation/client failure -> 502 (same mapping as adapters).
    if motif_specs:
        # Unify the seed: the engine falls back to `intent.seed` when request.seed is
        # None (candidates.py), so variant selection must see the SAME effective seed or
        # the two stages would diverge. `or 0` is intentionally avoided (it would conflate
        # an explicit seed=0 with None).
        effective_seed = (
            request.seed if request.seed is not None else int(intent_raw.get("seed") or 0)
        )
        intent_raw = _run_adapter(
            lambda: resolve_motifs(
                intent_raw,
                motif_specs,
                store=get_default_store(),
                embedding_client=get_default_embedding_client(),
                seed=effective_seed,
            )
        )

    started = time.perf_counter()
    try:
        result = generate_candidates(
            intent_raw,
            candidate_count=request.candidate_count,
            seed=request.seed,
            colorway=request.colorway,
            source_fidelity=source_fidelity,
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
    warnings = warnings + result.warnings

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
