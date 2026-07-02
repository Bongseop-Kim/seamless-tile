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

from contextlib import contextmanager
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Body, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.api.routes.finalize import finalize_candidate
from app.api.routes.generate import _run_adapter, respond_session_turn
from app.api.schemas.finalize import FinalizeRequest, FinalizeResponse
from app.api.schemas.generate import GenerateResponse
from app.sessions import graph as sg
from app.sessions.budget import SessionBusy, budget_exceeded, session_inflight
from app.sessions.store import upsert_session_row

router = APIRouter(prefix="/sessions", tags=["sessions"])


class SessionRestoreResponse(BaseModel):
    session_id: str
    turns: list[dict[str, Any]] = Field(default_factory=list)
    # [{id, png_url, colorway_id}] — no `intent` (§16: current_intent stays server-side,
    # consistent with the slim /generate response policy).
    candidates: list[dict[str, Any]] = Field(default_factory=list)
    pending: dict[str, Any] | None = None  # gate payload if paused (resume via select/confirm)
    seed: int | None = None
    colorway: str | None = None
    budget: dict[str, Any] = Field(default_factory=dict)


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


def _require_budget(session_id: str, kind: str) -> None:
    # Checked BEFORE resuming the graph / firing the expensive op: rejecting from inside
    # a resumed node would consume the interrupt and wedge the gate (S13; same reasoning
    # as the pre-resume motif-id check below).
    msg = budget_exceeded(sg.get_state(session_id).get("budget"), kind)
    if msg:
        raise HTTPException(status_code=429, detail=[msg])


@contextmanager
def _dedup(session_id: str):
    """Serialize mutating ops per session (S13 in-flight lock); a duplicate call while one
    is running gets a 409 instead of double-firing Recraft/finalize."""
    try:
        with session_inflight(session_id):
            yield
    except SessionBusy:
        raise HTTPException(
            status_code=409,
            detail=[f"session {session_id!r} already has an operation in flight"],
        ) from None


@router.get(
    "/{session_id}",
    response_model=SessionRestoreResponse,
    summary="Restore a session's conversation, committed candidates, and gate state",
)
async def get_session(session_id: str) -> SessionRestoreResponse:
    values = _run_adapter(lambda: sg.get_state(session_id))
    pending = _run_adapter(lambda: sg.pending_payload(session_id))
    if not values and pending is None:
        raise HTTPException(status_code=404, detail=[f"unknown session {session_id!r}"])
    candidates = [
        {k: c.get(k) for k in ("id", "png_url", "colorway_id")}
        for c in (values.get("current_candidates") or [])
    ]
    return SessionRestoreResponse(
        session_id=session_id,
        turns=values.get("turns") or [],
        candidates=candidates,
        pending=pending,
        seed=values.get("seed"),
        colorway=values.get("colorway"),
        budget=values.get("budget") or {},
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
    with _dedup(session_id):
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
        _require_budget(session_id, "recraft")
        with _dedup(session_id):
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
    _require_budget(session_id, "finalize")
    state = sg.get_state(session_id)
    candidates = state.get("current_candidates") or []
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
    with _dedup(session_id):
        resp = await finalize_candidate(fin)
    sg.increment_budget(session_id, "finalize_used")
    upsert_session_row(
        thread_id=session_id,
        status="finalized",
        seed=state.get("seed"),
        colorway=chosen.get("colorway_id") or state.get("colorway"),
        registry_version=state.get("registry_version"),
        current_intent=state.get("current_intent"),
    )
    return resp
