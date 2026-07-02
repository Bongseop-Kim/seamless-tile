"""LangGraph session graph (spec §4/§6/§8).

A thin authoring adapter: ``classify → author|edit → apply_tools → resolve_gate → validate
→ commit``. The gate is the only place an expensive op can happen, and only after an
``interrupt`` (human-in-the-loop confirm, §8.2). Everything below the committed intent is
the untouched deterministic engine. The checkpointer (``app.sessions.checkpointer``) owns
session state per ``thread_id == session_id`` -- ``MemorySaver`` when ``SUPABASE_DB_URL``
is unset, ``PostgresSaver`` (client-only, no DDL) when it is set (session 17, S7).

Nodes resolve their dependencies from the process-wide defaults (the same getters the
stateless route uses), so there is no injection plumbing and test resets apply uniformly.
"""

from __future__ import annotations

import copy

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from app.adapters.base import AdapterClientError
from app.adapters.edit_llm import resolve_edit_client, summarize_intent
from app.adapters.embedding import embed_query, get_default_embedding_client
from app.adapters.llm import build_intents
from app.adapters.motif_resolver import present_candidates, resolve_motifs
from app.adapters.recraft import generate_via_recraft, get_default_recraft_client
from app.adapters.registry_fingerprint import registry_version_for
from app.core.config import get_settings
from app.engine.candidates import SOURCE_FIDELITY_VECTOR, generate_candidate_set
from app.motifs.store import get_default_store
from app.sessions.checkpointer import checkpointer_from_settings, close_checkpointer
from app.sessions.state import SessionState, new_budget
from app.sessions.store import upsert_session_row
from app.sessions.tools import apply_tools
from app.validate.intent import IntentInvalid, validate_intent


# --- motif freeze (colors rebound to the concrete motif's slots) --------------


def _freeze_motif(intent: dict, layer_id: str, motif_id: str) -> None:
    """Set ``layer.params.motif_id`` and rebind its color spec to the concrete motif's
    ``color_slots`` so the committed intent passes ``validate_intent`` (mutates in place —
    callers pass a copy)."""
    from app.motifs.registry import get_motif

    palette_slots = [s["id"] for s in intent.get("palette", {}).get("slots", [])]
    try:
        slots = list(get_motif(motif_id).color_slots)
    except ValueError:
        slots = ["s0"]
    for layer in intent.get("layers", []):
        if layer.get("id") != layer_id:
            continue
        params = layer.setdefault("params", {})
        params["motif_id"] = motif_id
        default = palette_slots[0] if palette_slots else None
        if slots == ["s0"]:
            # Keep an existing *palette-slot* color (swap on an existing layer), but replace
            # a placeholder / motif-local token like "s0" (add_layer parks one) with a real
            # slot — else the frozen intent references a non-palette color and 422s.
            color = params.get("color")
            params["color"] = color if color in palette_slots else default
            params.pop("colors", None)
        else:
            existing = params.get("colors") or {}
            params["colors"] = {s: existing.get(s, default) for s in slots}
            params.pop("color", None)
        return


# --- nodes --------------------------------------------------------------------


def _classify(state: SessionState) -> str:
    # No committed intent yet → author (new); else edit. (Explicit "start over" routing is
    # a P1 nicety; the client can start a fresh session_id to author again.)
    return "edit" if state.get("current_intent") else "author"


def author_intent(state: SessionState) -> dict:
    adapted = build_intents(
        state["prompt"], images=state.get("images"), use_cache=False
    )[0]
    intent = adapted.intent
    seed = state.get("seed")
    if seed is None:
        seed = int(intent.get("seed") or 0)
    warnings = list(adapted.warnings or [])
    specs = list(adapted.motif_specs or [])
    # Text motifs (free deterministic glyph builder) and uploaded-image motifs (vectorize)
    # are resolved immediately by the resolver; only descriptor motifs (reuse/generate) go
    # through the confirm gate — the cost gate is about Recraft *generation* (S11/S12), and
    # routing text through the gate would both misfire Recraft and brick text authoring.
    def _is_immediate(s: dict) -> bool:
        return bool(s.get("text")) or s.get("source_image_index") is not None

    immediate = [s for s in specs if _is_immediate(s)]
    gated = [s for s in specs if not _is_immediate(s)]
    if immediate:
        intent = resolve_motifs(
            intent,
            immediate,
            store=get_default_store(),
            embedding_client=get_default_embedding_client(),
            recraft_client=get_default_recraft_client(),
            seed=seed,
            images=state.get("images"),
            warnings=warnings,
        )
    return {
        "working_intent": intent,
        "pending_specs": gated,
        "seed": seed,
        "warnings": warnings,
        "material_map": state.get("material_map") or {},
        "budget": state.get("budget") or new_budget(),
        "pending": None,
        "validate_errors": [],
        "edit_retried": False,
        "turns": (state.get("turns") or []) + [{"role": "user", "text": state["prompt"]}],
    }


def edit_intent(state: SessionState) -> dict:
    current = state["current_intent"]
    # On a validation-failure retry (spec §6.3), feed the prior errors back to the LLM so it
    # corrects the tool arguments — always re-applied to current_intent, never the failed one.
    errors = state.get("validate_errors") or []
    instruction = state["prompt"]
    if errors:
        instruction = (
            state["prompt"]
            + "\n\nThe previous edit failed validation:\n"
            + "\n".join(f"- {e}" for e in errors)
            + "\nAdjust the tool arguments to fix these."
        )
    tool_calls = resolve_edit_client().propose(summarize_intent(current), instruction)
    outcome = apply_tools(current, tool_calls)
    updates: dict = {
        "working_intent": outcome.intent,
        "pending_specs": list(outcome.motif_specs),
        "warnings": list(outcome.warnings),
        "pending": None,
        "validate_errors": [],
        "edit_retried": bool(errors),  # this pass IS the retry when errors were fed in
        "turns": (state.get("turns") or [])
        + [{"role": "user", "text": state["prompt"], "tool_calls": tool_calls}],
    }
    su = outcome.state_updates
    if "colorway" in su:
        updates["colorway"] = su["colorway"]
    if "seed" in su:
        updates["seed"] = su["seed"]
    if "material_map" in su:
        updates["material_map"] = {**(state.get("material_map") or {}), **su["material_map"]}
    return updates


def resolve_gate(state: SessionState) -> dict:
    """Resolve ONE pending motif spec behind the confirm gate. Presenting reuse candidates
    is free; ``generate_via_recraft`` (expensive) sits AFTER the interrupt, so it can only
    run once the user explicitly confirms "generate" (S11/S12). No-op when nothing pends."""
    specs = state.get("pending_specs") or []
    if not specs:
        return {}
    spec = specs[0]
    emb = get_default_embedding_client()
    candidates = (
        []
        if spec.get("force_new")
        else present_candidates(spec, store=get_default_store(), embedding_client=emb)
    )
    # Pause the turn (checkpoint). The route surfaces this payload as `pending`; the
    # select-motif / confirm endpoints resume with {action, motif_id?}. `budget` is a cost
    # hint only (S13) -- spend is always shown before the user confirms "generate".
    settings = get_settings()
    used = (state.get("budget") or {}).get("recraft_used", 0)
    decision = interrupt(
        {
            "type": "motif_candidates",
            "layer_id": spec.get("layer_id"),
            "spec": spec,
            "candidates": candidates,
            "budget": {
                "recraft_used": used,
                "recraft_limit": settings.session_recraft_limit,
            },
        }
    )
    # ---- resumes here ----
    action = (decision or {}).get("action")
    intent = copy.deepcopy(state["working_intent"])
    budget = dict(state.get("budget") or new_budget())
    if action == "select":
        motif_id = (decision or {}).get("motif_id")
        if not motif_id:
            raise AdapterClientError("select-motif requires a motif_id")
    elif action == "generate":
        embedding = embed_query(
            spec.get("description") or spec.get("subject") or "", client=emb
        )
        motif_id = generate_via_recraft(
            spec, client=get_default_recraft_client(), embedding=embedding
        )
        budget["recraft_used"] = budget.get("recraft_used", 0) + 1
    else:
        raise AdapterClientError(f"unknown gate action {action!r}")
    _freeze_motif(intent, spec["layer_id"], motif_id)
    return {"working_intent": intent, "pending_specs": specs[1:], "budget": budget}


def _gate_more(state: SessionState) -> str:
    return "resolve_gate" if state.get("pending_specs") else "validate"


def validate(state: SessionState) -> dict:
    # On failure, record the errors instead of raising: _after_validate routes to one edit
    # retry (edit turn only, spec §6.3), then to END where the route maps the carried
    # errors to a 422. Not raising keeps the checkpoint clean (no wedged thread).
    try:
        vr = validate_intent(state["working_intent"], repair=True)
    except IntentInvalid as exc:
        return {"validate_errors": list(exc.errors)}
    return {
        "working_intent": vr.intent.model_dump(mode="json"),
        "warnings": list(state.get("warnings") or []) + list(vr.warnings),
        "validate_errors": [],
    }


def _after_validate(state: SessionState) -> str:
    if not state.get("validate_errors"):
        return "commit"
    # One retry, edit turns only (author intents are pre-validated by build_intents).
    if state.get("current_intent") and not state.get("edit_retried"):
        return "retry"
    return "done"


def commit(state: SessionState) -> dict:
    intent = state["working_intent"]
    reg = registry_version_for(get_default_store())
    result = generate_candidate_set(
        [intent],
        candidate_count=state.get("candidate_count") or 1,
        seed=state.get("seed"),
        colorway=state.get("colorway"),
        source_fidelity=SOURCE_FIDELITY_VECTOR,
        registry_version=reg,
    )
    if not result.candidates:
        raise AdapterClientError("no candidate could be composed")
    # Best-effort mirror into `seamless_sessions` (monorepo-owned, S7/§11); never fails the
    # turn -- the checkpointer (this graph's state) is the restore source of truth.
    upsert_session_row(
        thread_id=state["session_id"],
        seed=state.get("seed"),
        colorway=state.get("colorway"),
        registry_version=reg,
        current_intent=intent,
    )
    render_batch = [
        {"id": rc.id, "svg": rc.candidate.svg, "tile_mm": rc.intent.canvas.tile_mm}
        for rc in result.candidates
    ]
    current_candidates = [
        {
            "id": rc.id,
            "colorway_id": rc.candidate.repro.colorway_id,
            "intent": rc.intent.model_dump(mode="json"),
        }
        for rc in result.candidates
    ]
    return {
        "current_intent": intent,
        "current_candidates": current_candidates,
        "render_batch": render_batch,
        "registry_version": reg,
        "warnings": list(state.get("warnings") or []) + list(result.warnings),
        "pending": None,
        "pending_specs": [],
        "working_intent": None,
        "validate_errors": [],
        "edit_retried": False,
        "turns": (state.get("turns") or [])
        + [{"role": "assistant", "text": f"{len(result.candidates)} candidate(s)"}],
    }


# --- graph singleton ----------------------------------------------------------


_GRAPH = None


def _build_graph():
    b = StateGraph(SessionState)
    b.add_node("author_intent", author_intent)
    b.add_node("edit_intent", edit_intent)
    b.add_node("resolve_gate", resolve_gate)
    b.add_node("validate", validate)
    b.add_node("commit", commit)
    b.add_conditional_edges(
        START, _classify, {"author": "author_intent", "edit": "edit_intent"}
    )
    b.add_edge("author_intent", "resolve_gate")
    b.add_edge("edit_intent", "resolve_gate")
    b.add_conditional_edges(
        "resolve_gate", _gate_more, {"resolve_gate": "resolve_gate", "validate": "validate"}
    )
    b.add_conditional_edges(
        "validate",
        _after_validate,
        {"commit": "commit", "retry": "edit_intent", "done": END},
    )
    b.add_edge("commit", END)
    return b.compile(checkpointer=checkpointer_from_settings(get_settings()))


def _graph():
    global _GRAPH
    if _GRAPH is None:
        _GRAPH = _build_graph()
    return _GRAPH


def reset_sessions() -> None:
    """Drop the session graph + its checkpointer (test isolation / ops)."""
    global _GRAPH
    _GRAPH = None
    close_checkpointer()


# --- entrypoints --------------------------------------------------------------


def _cfg(session_id: str, checkpoint_id: str | None = None) -> dict:
    configurable = {"thread_id": session_id}
    if checkpoint_id is not None:
        configurable["checkpoint_id"] = checkpoint_id
    return {"configurable": configurable}


def run_turn(
    session_id: str,
    *,
    prompt: str,
    images: list[bytes] | None = None,
    seed: int | None = None,
    colorway: str | None = None,
    candidate_count: int = 1,
    from_checkpoint: str | None = None,
) -> dict:
    """Run one turn (author or edit — the graph classifies). Returns the raw graph result;
    a paused turn carries ``__interrupt__`` (read it with :func:`pending_of`).

    ``from_checkpoint`` (session 18, time-travel fork) invokes against an earlier
    checkpoint instead of the thread head: LangGraph forks a new branch from it, leaving
    the original branch's checkpoints untouched. The budget is carried forward from the
    thread HEAD (not the fork point) — it is a cost-guard trust boundary, so forking to an
    earlier, lower counter must never refund spend.
    """
    turn_input: dict = {
        "session_id": session_id,
        "prompt": prompt,
        "images": images,
        "candidate_count": candidate_count,
    }
    if seed is not None:
        turn_input["seed"] = seed
    if colorway is not None:
        turn_input["colorway"] = colorway
    if from_checkpoint:
        head_budget = get_state(session_id).get("budget")
        if head_budget:
            turn_input["budget"] = head_budget
    return _graph().invoke(turn_input, config=_cfg(session_id, from_checkpoint))


def resume_turn(session_id: str, resume_value: dict) -> dict:
    """Resume a paused turn at the gate with a decision ``{action, motif_id?}``."""
    return _graph().invoke(Command(resume=resume_value), config=_cfg(session_id))


def pending_of(result: dict) -> dict | None:
    """The gate payload if the turn paused, else ``None``."""
    interrupts = result.get("__interrupt__")
    if not interrupts:
        return None
    return interrupts[0].value


def get_state(session_id: str, checkpoint_id: str | None = None) -> dict:
    """Session values at ``checkpoint_id`` (thread head by default). Empty dict for an
    unknown session or checkpoint — a read-only restore, never a head move (undo/redo,
    session 18)."""
    return dict(_graph().get_state(_cfg(session_id, checkpoint_id)).values)


def list_turn_checkpoints(session_id: str) -> list[dict]:
    """Turn-boundary checkpoints, oldest first: ``[{checkpoint_id, created_at, turns,
    prompt}]`` for undo/redo/fork (session 18). Filtered to loop-end commits (excludes
    in-turn ``update_state`` writes, failed/validation-error turns, and gate pauses), so
    each committed turn yields exactly one entry; ``turns`` is the turn count at that
    checkpoint and ``prompt`` is its last user message."""
    snapshots = [
        snap
        for snap in _graph().get_state_history(_cfg(session_id))
        if snap.next == ()
        and (snap.metadata or {}).get("source") == "loop"
        and snap.values.get("current_intent")
        and not snap.values.get("validate_errors")
        and not snap.interrupts
    ]
    out = []
    for snap in reversed(snapshots):  # oldest first
        turns = snap.values.get("turns") or []
        prompt = next(
            (t.get("text") for t in reversed(turns) if t.get("role") == "user"), None
        )
        out.append(
            {
                "checkpoint_id": snap.config["configurable"]["checkpoint_id"],
                "created_at": snap.created_at,
                "turns": len(turns),
                "prompt": prompt,
            }
        )
    return out


def awaiting_gate(session_id: str) -> bool:
    """Whether the session is paused at the motif confirm gate (resumable).

    Also true when the gate node raised AFTER its interrupt (e.g. a transient Recraft
    outage): LangGraph then leaves ``next == ()`` but the task keeps its pending interrupt,
    which is still resumable (retrying the same decision succeeds once the outage clears).
    """
    snap = _graph().get_state(_cfg(session_id))
    if snap.next and "resolve_gate" in snap.next:
        return True
    return any(getattr(t, "interrupts", None) for t in (snap.tasks or ()))


def set_candidate_previews(session_id: str, candidates: list[dict]) -> None:
    """Write rendered ``current_candidates`` (with png_urls) back and drop the transient
    render batch."""
    _graph().update_state(
        _cfg(session_id), {"current_candidates": candidates, "render_batch": []}
    )


def pending_payload(session_id: str) -> dict | None:
    """The live gate interrupt payload from the checkpoint, or ``None`` when not paused --
    lets ``GET /sessions/{id}`` re-render the confirm UI after a process restart."""
    snap = _graph().get_state(_cfg(session_id))
    for task in snap.tasks or ():
        interrupts = getattr(task, "interrupts", None)
        if interrupts:
            return interrupts[0].value
    return None


def increment_budget(session_id: str, key: str) -> None:
    """Bump a budget counter outside the graph (e.g. ``finalize_used``): finalize runs via
    its own route, not a graph node, so it must record its spend explicitly."""
    budget = dict(get_state(session_id).get("budget") or new_budget())
    budget[key] = budget.get(key, 0) + 1
    _graph().update_state(_cfg(session_id), {"budget": budget})
