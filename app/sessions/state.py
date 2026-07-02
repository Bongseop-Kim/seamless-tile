"""Session state schema (spec §5).

The LangGraph state IS the session: a ``MemorySaver`` owns it per ``thread_id ==
session_id`` (P0 is in-memory; Postgres persistence is P1). ``TypedDict`` with
``total=False`` so nodes return partial updates that shallow-merge.
"""

from __future__ import annotations

from typing import Any, TypedDict


class SessionState(TypedDict, total=False):
    session_id: str

    # --- turn input (merged in on each invoke) ---
    prompt: str
    images: list[bytes] | None
    candidate_count: int

    # --- committed session (the repro anchor + restore/finalize targets, §5) ---
    current_intent: dict | None  # last committed frozen resolved intent
    current_candidates: list[dict]  # [{id, png_url, intent, colorway_id}] for restore/finalize
    seed: int
    colorway: str | None
    registry_version: str
    material_map: dict  # set_material writes here only — never the engine intent (§7)
    budget: dict  # {"recraft_used": int} — counter only in P0; enforcement is P1 (S13)
    turns: list[dict]  # {role, text, tool_calls?, gate?} — minimal history for edit summary

    # --- gate / in-flight (§8) ---
    pending: dict | None  # surfaced to the client while awaiting a gate decision
    working_intent: dict | None  # in-progress intent before commit
    pending_specs: list[dict]  # motif specs still awaiting the resolve gate
    validate_errors: list[str]  # carried out to a 422 when the (retried) edit won't validate
    edit_retried: bool  # guards the single validation-failure re-prompt (§6.3)

    # --- transient hand-off to the HTTP layer (rendered once, then cleared) ---
    render_batch: list[dict]  # [{id, svg, tile_mm}] the route rasterizes to previews
    warnings: list[str]


def new_budget() -> dict[str, Any]:
    return {"recraft_used": 0}
