"""Session action endpoints (spec §10, S8): structured gate decisions — not free text.

- ``POST /sessions/{id}/select-motif`` freezes an existing (free) motif and commits.
- ``POST /sessions/{id}/confirm`` either approves the expensive Recraft generation
  (``generate_motif``) or hands the chosen candidate to the finalize (fabric) render
  (``finalize`` — free, local, deterministic; a UX decision, not a cost gate, §8.4).

The turn itself runs in the LangGraph session graph; these endpoints only resume it with
a decision or trigger the finalize hand-off. Recraft can never fire without an explicit
``generate_motif`` confirm; the fabric render can never fire without ``finalize``.
"""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Body, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.api.routes.finalize import finalize_candidate
from app.api.routes.generate import _run_adapter, respond_session_turn
from app.api.schemas.finalize import FinalizeRequest, FinalizeResponse
from app.api.schemas.generate import GenerateResponse
from app.sessions import graph as sg

router = APIRouter(prefix="/sessions", tags=["sessions"])


class SelectMotifRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    layer_id: str = Field(min_length=1)
    motif_id: str = Field(min_length=1)


class ConfirmRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["generate_motif", "finalize"]
    # finalize-only (all optional): which committed candidate + fabric knobs.
    candidate_id: str | None = None
    colorway_id: str | None = None
    weave: str | None = None
    material_map: dict[str, str] | None = None


def _require_gate(session_id: str) -> None:
    if not sg.awaiting_gate(session_id):
        raise HTTPException(
            status_code=409,
            detail=[f"session {session_id!r} is not awaiting a motif decision"],
        )


@router.post(
    "/{session_id}/select-motif",
    response_model=GenerateResponse,
    summary="Freeze an existing (free) motif candidate and continue the turn",
)
async def select_motif(
    session_id: str, request: Annotated[SelectMotifRequest, Body()]
) -> GenerateResponse:
    _require_gate(session_id)
    # Validate the motif exists BEFORE resuming: LangGraph caches the resume value against
    # the interrupt, so resuming with a bad id would consume the gate and wedge the turn
    # (and a bad client id is a 4xx, not the 502 that a downstream compose failure yields).
    from app.motifs.registry import get_motif

    try:
        get_motif(request.motif_id)
    except ValueError:
        raise HTTPException(
            status_code=404, detail=[f"unknown motif_id {request.motif_id!r}"]
        ) from None
    result = _run_adapter(
        lambda: sg.resume_turn(
            session_id, {"action": "select", "motif_id": request.motif_id}
        )
    )
    return await respond_session_turn(session_id, result)


@router.post(
    "/{session_id}/confirm",
    response_model=None,
    summary="Approve Recraft generation (generate_motif) or trigger the fabric finalize",
)
async def confirm(session_id: str, request: Annotated[ConfirmRequest, Body()]):
    if request.action == "generate_motif":
        _require_gate(session_id)
        result = _run_adapter(
            lambda: sg.resume_turn(session_id, {"action": "generate"})
        )
        return await respond_session_turn(session_id, result)

    # finalize: hand the chosen committed candidate to the (free, local) fabric render.
    # Reject while an edit turn is paused at the gate — current_candidates would still be
    # the PREVIOUS turn's, so finalizing now would render a stale candidate.
    if sg.awaiting_gate(session_id):
        raise HTTPException(
            status_code=409,
            detail=[f"session {session_id!r} has a pending motif decision; resolve it first"],
        )
    candidates = sg.get_state(session_id).get("current_candidates") or []
    if not candidates:
        raise HTTPException(
            status_code=404, detail=[f"session {session_id!r} has no committed candidate"]
        )
    chosen = candidates[0]
    if request.candidate_id is not None:
        chosen = next(
            (c for c in candidates if c.get("id") == request.candidate_id), None
        )
        if chosen is None:
            raise HTTPException(
                status_code=404, detail=[f"unknown candidate {request.candidate_id!r}"]
            )
    # ponytail: P0 forwards explicit finalize knobs (or defaults); translating the rich
    # set_material session map into finalize's {slot: weave} form is session-15/P1 work.
    fin = FinalizeRequest(
        intent=chosen["intent"],
        colorway_id=request.colorway_id or chosen.get("colorway_id"),
        material_map=request.material_map,
        **({"weave": request.weave} if request.weave else {}),
    )
    return await finalize_candidate(fin)
