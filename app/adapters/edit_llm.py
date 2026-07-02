"""Edit-turn tool-use seam (spec §7, S5): bind the whitelist to the LLM, get tool calls.

Kept separate from the authoring ``LLMClient`` (``app/adapters/llm.py``): authoring emits
intent JSON (JSON mode), editing emits **tool calls** from the closed whitelist. We use
LangChain ``bind_tools`` (``langchain-google-genai``) so tool selection rides the same
ecosystem as the LangGraph session graph — but ``bind_tools`` only delegates *selection*;
the whitelist enforcement, argument validation and deterministic apply stay in
``app.sessions.tools`` (spec §16). The LLM tool choice is non-deterministic by design
(spec §9); only ``apply_tools`` downstream must be pure.

The seam is a ``Protocol`` so tests inject a scripted fake (``tests/_fakes``), mirroring
``LLMClient``. Missing key → ``EditLLMNotConfigured`` (a 502-class error), like the store.
"""

from __future__ import annotations

import json
from typing import Protocol

from app.adapters.base import AdapterClientError
from app.sessions.tools import TOOL_NAMES

DEFAULT_MODEL = "gemini-2.5-flash-lite"


class EditLLMNotConfigured(AdapterClientError):
    """No edit LLM injected and none configured (no GEMINI_API_KEY)."""


class EditLLM(Protocol):
    """Propose whitelist tool calls for an edit instruction. Returns a list of
    ``{"name": str, "args": dict}`` (possibly empty)."""

    def propose(self, summary: str, instruction: str) -> list[dict]: ...


# Argument schemas for the closed whitelist (spec §7), in OpenAI function-tool form so
# ``bind_tools`` accepts them across providers. The *shapes* mirror app.sessions.tools;
# that module — not the LLM — is the enforcement point.
_P = {
    "set_colorway": {
        "type": "object",
        "properties": {"colorway_id": {"type": "string"}},
        "required": ["colorway_id"],
    },
    "set_palette_slot": {
        "type": "object",
        "properties": {
            "slot_id": {"type": "string"},
            "hex": {"type": "string", "description": "#RRGGBB"},
        },
        "required": ["slot_id", "hex"],
    },
    "scale_motif": {
        "type": "object",
        "properties": {
            "layer_id": {"type": "string"},
            "factor": {"type": "number", "description": "multiplier on motif size_mm"},
        },
        "required": ["layer_id", "factor"],
    },
    "set_stripe": {
        "type": "object",
        "properties": {
            "layer_id": {"type": "string"},
            "angle": {"type": "number"},
            "period_mm": {"type": "number"},
        },
        "required": ["layer_id"],
    },
    "set_density": {
        "type": "object",
        "properties": {
            "layer_id": {"type": "string"},
            "spacing_mm": {"type": "number"},
        },
        "required": ["layer_id", "spacing_mm"],
    },
    "add_layer": {
        "type": "object",
        "properties": {
            "layer": {"type": "object", "description": "a layer dict (id, type, params, ...)"},
            "motif": {"type": "object", "description": "facets when the layer is a new motif"},
            "force_new": {"type": "boolean"},
        },
        "required": ["layer"],
    },
    "remove_layer": {
        "type": "object",
        "properties": {"layer_id": {"type": "string"}},
        "required": ["layer_id"],
    },
    "swap_motif": {
        "type": "object",
        "properties": {
            "layer_id": {"type": "string"},
            "description": {"type": "string"},
            "subject": {"type": "string"},
            "scope": {"type": "string", "enum": ["whole", "partial"]},
            "prefer_reuse": {"type": "boolean"},
            "force_new": {"type": "boolean"},
        },
        "required": ["layer_id", "description"],
    },
    "set_seed": {
        "type": "object",
        "properties": {"seed": {"type": "integer"}},
        "required": ["seed"],
    },
    "regenerate": {"type": "object", "properties": {}},
    "set_material": {
        "type": "object",
        "properties": {
            "target": {"type": "string", "description": "a layer id or palette slot id"},
            "fabric": {"type": "string"},
            "finish": {"type": "string"},
            "lighting": {"type": "string"},
        },
        "required": ["target"],
    },
}

_DESCRIPTIONS = {
    "set_colorway": "Switch the active colorway.",
    "set_palette_slot": "Change one palette slot's hex color.",
    "scale_motif": "Scale a motif layer's size by a factor.",
    "set_stripe": "Change a stripe layer's angle and/or period.",
    "set_density": "Change a motif layer's placement spacing.",
    "add_layer": "Add a new layer (background, stripe, or motif).",
    "remove_layer": "Remove a layer by id.",
    "swap_motif": "Replace a motif layer's motif with a described one (presents reuse "
    "candidates; generation is confirmed separately).",
    "set_seed": "Re-roll the deterministic variant seed.",
    "regenerate": "Re-emit candidates from the current design.",
    "set_material": "Set a fabric/finish/lighting material on a layer or slot (used only "
    "at fabric finalize; does not change the design).",
}

TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": name,
            "description": _DESCRIPTIONS[name],
            "parameters": _P[name],
        },
    }
    for name in TOOL_NAMES
]

_EDIT_SYSTEM = (
    "You edit an existing seamless-tile design by calling the provided tools. Make the "
    "smallest change that satisfies the user's request: call ONLY the tools needed, and "
    "touch only the layers/slots named. Reference layers and palette slots by their "
    "stable ids from the design summary. Do not describe changes in prose — call tools."
)


class GeminiEditLLM:
    """LangChain ``ChatGoogleGenerativeAI`` with the whitelist bound. temperature 0."""

    def __init__(self, api_key: str, model: str = DEFAULT_MODEL, *, temperature: float = 0.0) -> None:
        if not api_key:
            raise EditLLMNotConfigured("GeminiEditLLM requires a non-empty api_key")
        # Lazy import: keeps module import light and lets tests run without the provider.
        from langchain_google_genai import ChatGoogleGenerativeAI

        self._llm = ChatGoogleGenerativeAI(
            model=model, google_api_key=api_key, temperature=temperature
        ).bind_tools(TOOL_SCHEMAS)

    def propose(self, summary: str, instruction: str) -> list[dict]:
        from langchain_core.messages import HumanMessage, SystemMessage

        try:
            msg = self._llm.invoke(
                [
                    SystemMessage(_EDIT_SYSTEM),
                    HumanMessage(f"Design summary:\n{summary}\n\nEdit request:\n{instruction}"),
                ]
            )
        except Exception as exc:  # transport / SDK failure → 502 like other adapters
            raise AdapterClientError(f"edit LLM request failed: {exc}") from exc
        return [{"name": tc["name"], "args": tc.get("args") or {}} for tc in (msg.tool_calls or [])]


_DEFAULT_EDIT_CLIENT: EditLLM | None = None


def set_default_edit_client(client: EditLLM | None) -> None:
    global _DEFAULT_EDIT_CLIENT
    _DEFAULT_EDIT_CLIENT = client


def get_default_edit_client() -> EditLLM | None:
    return _DEFAULT_EDIT_CLIENT


def resolve_edit_client(client: EditLLM | None = None) -> EditLLM:
    resolved = client or _DEFAULT_EDIT_CLIENT
    if resolved is None:
        raise EditLLMNotConfigured(
            "no edit LLM configured; set GEMINI_API_KEY or inject via set_default_edit_client(...)"
        )
    return resolved


def client_from_settings(settings) -> EditLLM | None:
    api_key = getattr(settings, "gemini_api_key", None)
    if not api_key:
        return None
    model = getattr(settings, "gemini_model", None) or DEFAULT_MODEL
    return GeminiEditLLM(api_key, model, temperature=0.0)


def summarize_intent(intent: dict) -> str:
    """A compact, stable-keyed summary for the edit prompt — layers/palette/colorways with
    their ids and key params (spec §5). Deterministic (preserves intent order, no clock/
    randomness) and far smaller than the full intent."""
    palette = [
        {"slot_id": s.get("id"), "hex": s.get("hex")}
        for s in (intent.get("palette") or {}).get("slots", [])
    ]
    layers = []
    for la in intent.get("layers", []):
        params = la.get("params") or {}
        entry = {"layer_id": la.get("id"), "type": la.get("type")}
        if la.get("type") == "stripe":
            entry["angle"] = params.get("angle")
            entry["period_mm"] = params.get("period_mm")
        elif la.get("type") == "motif":
            entry["motif_id"] = params.get("motif_id")
            entry["size_mm"] = params.get("size_mm")
            if la.get("placement"):
                entry["placement"] = (la["placement"]).get("type")
        elif la.get("type") == "background":
            entry["color"] = params.get("color")
        layers.append(entry)
    summary = {
        "canvas": intent.get("canvas"),
        "colorways": [c.get("id") for c in intent.get("colorways", [])],
        "palette": palette,
        "layers": layers,
    }
    return json.dumps(summary, ensure_ascii=False, sort_keys=False)
