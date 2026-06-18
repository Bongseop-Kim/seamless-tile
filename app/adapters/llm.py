"""LLM adapter: ``prompt -> intent`` JSON.

The LLM client is an injected seam (a ``Protocol``), NOT a hard dependency: no SDK
is added to ``requirements.txt``. Tests inject a fake client; real network calls are
opt-in (configure a default client via :func:`set_default_client`). The adapter only
produces intent JSON — never raw SVG or coordinates.

On stage-0 validation failure the adapter does ONE constrained re-prompt (feeding the
errors back), then gives up with ``IntentInvalid`` (the route maps that to 422). This
re-prompt is an authoring/validation-time step and lives OUTSIDE the determinism
boundary; only the finalized intent is cached and subject to the determinism contract.
"""

from __future__ import annotations

import copy
import json
from typing import Protocol, runtime_checkable

from app.adapters.base import AdapterClientError, AdapterResult, cache_key
from app.motifs.registry import MOTIFS
from app.validate.intent import IntentInvalid, validate_intent

DEFAULT_TILE_MM = 48.0
DEFAULT_DPI = 300


@runtime_checkable
class LLMClient(Protocol):
    """Minimal text-completion seam. Kept tiny so a real SDK can back it later
    without churning the signature (no streaming/tool-use leakage into the core)."""

    def complete(self, prompt: str) -> str: ...


class LLMNotConfigured(AdapterClientError):
    """No LLM client was injected and none is configured as the default."""


_DEFAULT_CLIENT: LLMClient | None = None


def set_default_client(client: LLMClient | None) -> None:
    """Register a process-wide default client (opt-in; used for real calls)."""
    global _DEFAULT_CLIENT
    _DEFAULT_CLIENT = client


def _resolve_client(client: LLMClient | None) -> LLMClient:
    if client is not None:
        return client
    if _DEFAULT_CLIENT is not None:
        return _DEFAULT_CLIENT
    raise LLMNotConfigured(
        "no LLM client configured; inject one via build_intent(client=...) or "
        "set_default_client(...). Network calls are opt-in — session 7 mocks all externals."
    )


# Process-local freeze cache: same inputs -> same finalized intent -> same SVG.
_intent_cache: dict[str, dict] = {}


def clear_intent_cache() -> None:
    _intent_cache.clear()


# A compact, known-seamless example handed to the model as the target shape. Inlined
# (not imported from tests) so the app has no test dependency.
_EXAMPLE_INTENT = {
    "intent_version": 1,
    "canvas": {"tile_mm": 48, "dpi": 300},
    "seed": 0,
    "production": {"method": "digital", "max_colors": 12},
    "palette": {
        "slots": [
            {"id": "ground", "hex": "#10243a"},
            {"id": "accent", "hex": "#ef8a7a"},
        ]
    },
    "colorways": [
        {"id": "default", "name": "default", "mapping": {"ground": "#10243a", "accent": "#ef8a7a"}}
    ],
    "layers": [
        {"id": "ground", "type": "background", "z_order": 0, "params": {"color": "ground"}},
        {
            "id": "stripe_base",
            "type": "stripe",
            "z_order": 1,
            "params": {
                "angle": -36.87,
                "period_mm": 9.6,
                "bands": [{"offset_mm": 0, "width_mm": 4.8, "color": "accent"}],
            },
        },
        {
            "id": "dot_lane",
            "type": "motif",
            "z_order": 2,
            "params": {"motif_id": "circle", "size_mm": 1.4, "color": "accent"},
            "placement": {
                "type": "path_following",
                "host_layer": "stripe_base",
                "lane": "center",
                "spacing_mm": 6,
                "phase_mm": 0,
            },
        },
    ],
}


def _build_prompt(
    user_prompt: str,
    *,
    canvas: dict | None,
    palette: dict | None,
    errors: list[str] | None,
) -> str:
    motif_ids = ", ".join(sorted(MOTIFS))
    target_canvas = canvas or {"tile_mm": DEFAULT_TILE_MM, "dpi": DEFAULT_DPI}
    lines = [
        "You convert a textile pattern description into intent JSON for a seamless "
        "SVG engine. The engine handles all geometry, repetition and seamlessness.",
        "Output ONLY one JSON object — no SVG, no coordinates, no markdown, no prose.",
        "",
        "Target shape (match exactly):",
        json.dumps(_EXAMPLE_INTENT, ensure_ascii=False, indent=2),
        "",
        "Constraints:",
        "- intent_version must be 1.",
        f"- motif_id must be one of: {motif_ids}.",
        "- layer params colors reference palette slot ids, never raw hex.",
        "- a colorway with id 'default' is required; its mapping covers every slot.",
        "- period_mm must divide tile_mm; motif placement spacing_mm must divide tile_mm.",
        "- diagonal stripes are the default (necktie domain); the engine snaps the "
        "angle to a rational tile slope, so -36.87 (a 3/4 slope) with period_mm = "
        "tile_mm/5 is always seamless.",
        f"- target canvas: {json.dumps(target_canvas)}.",
    ]
    if palette:
        lines.append(f"- preferred palette hint: {json.dumps(palette)}.")
    lines += ["", f"Description: {user_prompt}"]
    if errors:
        lines += ["", "Your previous attempt FAILED stage-0 validation. Fix exactly these:"]
        lines += [f"- {e}" for e in errors]
    return "\n".join(lines)


def build_intent(
    prompt: str,
    *,
    canvas: dict | None = None,
    palette: dict | None = None,
    client: LLMClient | None = None,
    use_cache: bool = True,
) -> AdapterResult:
    """Turn a text prompt into a validated, frozen intent dict.

    Raises :class:`IntentInvalid` if the model cannot produce a valid intent within
    the initial attempt plus one constrained re-prompt, and
    :class:`LLMNotConfigured` if no client is available.
    """
    key = cache_key({"k": "llm", "prompt": prompt, "canvas": canvas, "palette": palette})
    if use_cache and key in _intent_cache:
        c = _intent_cache[key]
        # Hand back independent copies so a mutating caller can't corrupt the freeze,
        # and replay the stage-0 warnings (so the same request keeps the same warnings).
        return AdapterResult(
            intent=copy.deepcopy(c["intent"]),
            source_fidelity="vector",
            warnings=list(c["warnings"]),
        )

    llm = _resolve_client(client)

    errors: list[str] | None = None
    last_exc: IntentInvalid | None = None
    for _ in range(2):  # initial attempt + one constrained re-prompt
        text = llm.complete(_build_prompt(prompt, canvas=canvas, palette=palette, errors=errors))
        try:
            raw = json.loads(text)
        except (json.JSONDecodeError, TypeError) as exc:
            last_exc = IntentInvalid([f"LLM response was not valid JSON: {exc}"])
            errors = last_exc.errors
            continue
        if not isinstance(raw, dict):
            last_exc = IntentInvalid(["LLM response JSON was not an object"])
            errors = last_exc.errors
            continue
        raw.setdefault("intent_version", 1)
        try:
            result = validate_intent(raw, repair=True)
        except IntentInvalid as exc:
            last_exc = exc
            errors = exc.errors
            continue
        frozen = result.intent.model_dump(mode="json")
        warns = list(result.warnings)
        if use_cache:
            _intent_cache[key] = {"intent": copy.deepcopy(frozen), "warnings": warns}
        return AdapterResult(
            intent=frozen, source_fidelity="vector", warnings=list(warns)
        )

    assert last_exc is not None  # the loop only exits early via return
    raise last_exc
