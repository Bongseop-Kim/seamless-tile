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
import re
from typing import Protocol, runtime_checkable

from app.adapters.base import AdapterClientError, AdapterResult, cache_key
from app.motifs import facets
from app.motifs.registry import MOTIFS, normalize_motif_svg, register_motif
from app.render.sanitize import SanitizeError
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
    target_canvas = canvas or {"tile_mm": DEFAULT_TILE_MM, "dpi": DEFAULT_DPI}
    builtin_ids = ", ".join(sorted(MOTIFS))
    part_vocab = ", ".join(sorted(facets.PART_VOCAB))
    example = {
        "intent": _EXAMPLE_INTENT,
        "motif_specs": [
            {
                "layer_id": "dot_lane",
                "subject": "circle",
                "part": "whole",
                "view": "front",
                "style": "flat",
                "description": "small solid dot",
                "complexity": "simple",
            }
        ],
    }
    lines = [
        "You convert a textile pattern description into intent JSON for a seamless "
        "SVG engine. The engine handles all geometry, repetition and seamlessness.",
        'Output ONLY one JSON object with two keys: "intent" and "motif_specs" — '
        "no SVG, no coordinates, no markdown, no prose.",
        "",
        "Target shape (match exactly):",
        json.dumps(example, ensure_ascii=False, indent=2),
        "",
        "Constraints:",
        "- intent.intent_version must be 1.",
        "- For EACH motif layer in intent.layers, set params.motif_id to that layer's "
        "id (a placeholder the resolver replaces) and add a matching motif_specs entry "
        "whose layer_id equals the layer id. Do NOT invent registry ids.",
        f"- You MAY instead reference a built-in motif directly (motif_id one of: "
        f"{builtin_ids}); omit its motif_specs entry if you do.",
        "- Each motif_specs entry needs: subject (free text, required), part "
        f"(REQUIRED, one of: {part_vocab}), optional view/expression/style, and a short "
        "English description used for retrieval.",
        '- Optionally add "complexity": "detailed" for painterly / multi-color motifs '
        '(routed to the Recraft generator), or "simple" for single-color geometric '
        "motifs (the default; routed to the LLM).",
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


def _strip_code_fence(text: str) -> str:
    """Drop a ```/```json markdown fence if the model wrapped its JSON in one.

    Best-effort: only strips when the text clearly opens with a fence, so clean JSON
    (the test fakes, a well-behaved model) is untouched.
    """
    s = text.strip()
    if not s.startswith("```"):
        return s
    match = re.fullmatch(
        r"```[ \t]*(?:[A-Za-z0-9_-]+)?[ \t]*(?:\r?\n)?(?P<body>.*?)```",
        s,
        flags=re.DOTALL,
    )
    return match.group("body").strip() if match else s


def _split_intent_and_specs(raw: dict) -> tuple[dict, list[dict]]:
    """Accept either the wrapper ``{"intent": {...}, "motif_specs": [...]}`` or a bare
    intent dict (legacy / image path / no specs). Returns ``(intent_dict, specs)``.

    Detection is unambiguous: a valid intent never has a top-level ``intent`` key
    (``Intent`` is ``extra="forbid"``), so a dict object under ``intent`` marks the
    wrapper shape.
    """
    if isinstance(raw.get("intent"), dict):
        intent = raw["intent"]
        specs = raw.get("motif_specs")
    else:
        intent, specs = raw, None
    if not isinstance(specs, list):
        specs = []
    return intent, [s for s in specs if isinstance(s, dict)]


def _validate_spec_facets(specs: list[dict]) -> list[str]:
    """Validate motif-spec facets against the controlled vocab (M2). ``part`` is
    controlled (``facets.PART_VOCAB``); ``subject`` is open in P0 but required. Returns
    a list of error strings (empty == valid) fed back into the one re-prompt."""
    errors: list[str] = []
    for i, spec in enumerate(specs):
        layer_id = spec.get("layer_id")
        if not isinstance(layer_id, str) or not layer_id:
            errors.append(f"motif_specs[{i}] missing string 'layer_id'")
        subject = spec.get("subject")
        if not isinstance(subject, str) or not subject.strip():
            errors.append(f"motif_specs[{i}] missing non-empty 'subject'")
            subject = None
        part = spec.get("part")
        if not isinstance(part, str) or not part.strip():
            errors.append(
                f"motif_specs[{i}] missing 'part' (one of {sorted(facets.PART_VOCAB)})"
            )
            continue
        for field in ("view", "expression", "style", "description"):
            value = spec.get(field)
            if value is not None and not isinstance(value, str):
                errors.append(f"motif_specs[{i}] field '{field}' must be a string")
        try:
            facets.validate_facets(subject, part)
        except ValueError as exc:
            errors.append(f"motif_specs[{i}]: {exc}")
    return errors


def build_intent(
    prompt: str,
    *,
    canvas: dict | None = None,
    palette: dict | None = None,
    client: LLMClient | None = None,
    use_cache: bool = True,
) -> AdapterResult:
    """Turn a text prompt into a validated, frozen intent dict + motif specs.

    The model emits ``{"intent": ..., "motif_specs": [...]}`` (a bare intent without
    specs is still accepted for the legacy path). ``motif_specs`` carry per-layer
    facets (subject/part/...) for the deterministic motif resolver to act on; their
    ``part`` facet is validated against the controlled vocabulary here.

    Raises :class:`IntentInvalid` if the model cannot produce a valid intent (or valid
    facets) within the initial attempt plus one constrained re-prompt, and
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
            motif_specs=copy.deepcopy(c["motif_specs"]),
        )

    llm = _resolve_client(client)

    errors: list[str] | None = None
    last_exc: IntentInvalid | None = None
    for _ in range(2):  # initial attempt + one constrained re-prompt
        text = llm.complete(_build_prompt(prompt, canvas=canvas, palette=palette, errors=errors))
        try:
            raw = json.loads(_strip_code_fence(text))
        except (json.JSONDecodeError, TypeError) as exc:
            last_exc = IntentInvalid([f"LLM response was not valid JSON: {exc}"])
            errors = last_exc.errors
            continue
        if not isinstance(raw, dict):
            last_exc = IntentInvalid(["LLM response JSON was not an object"])
            errors = last_exc.errors
            continue
        intent_raw, specs = _split_intent_and_specs(raw)
        intent_raw.setdefault("intent_version", 1)
        facet_errors = _validate_spec_facets(specs)
        if facet_errors:
            last_exc = IntentInvalid(facet_errors)
            errors = facet_errors
            continue
        try:
            result = validate_intent(intent_raw, repair=True)
        except IntentInvalid as exc:
            last_exc = exc
            errors = exc.errors
            continue
        frozen = result.intent.model_dump(mode="json")
        warns = list(result.warnings)
        specs_frozen = [dict(s) for s in specs]
        if use_cache:
            _intent_cache[key] = {
                "intent": copy.deepcopy(frozen),
                "warnings": warns,
                "motif_specs": copy.deepcopy(specs_frozen),
            }
        return AdapterResult(
            intent=frozen,
            source_fidelity="vector",
            warnings=list(warns),
            motif_specs=specs_frozen,
        )

    assert last_exc is not None  # the loop only exits early via return
    raise last_exc


# --- miss-path motif generation (single-color SVG; D8 simple=LLM) ------------

# Process-local freeze cache for generated motif SVGs: same spec -> same SVG -> same
# content-hash motif_id (the determinism contract; spec §9.4).
_motif_svg_cache: dict[str, str] = {}


def clear_motif_svg_cache() -> None:
    _motif_svg_cache.clear()


_SVG_RE = re.compile(r"<svg\b.*?</svg>", re.DOTALL | re.IGNORECASE)


def _extract_svg(text: str) -> str:
    """Pull the ``<svg>...</svg>`` body out of a completion (tolerate stray fences/prose)."""
    if not isinstance(text, str):
        return ""
    match = _SVG_RE.search(text)
    return match.group(0) if match else text.strip()


def _canonical_spec(spec: dict) -> dict:
    """The normalized facet subset that determines the rendered motif (cache key/freeze).

    Normalizing (NFC/strip/casefold) collapses trivial text variants to one frozen entry
    so equivalent specs reuse the same generated motif id."""
    return {
        k: facets.normalize_facet(spec.get(k))
        for k in ("subject", "part", "view", "expression", "style", "description")
    }


def _build_svg_prompt(spec: dict, *, errors: list[str] | None = None) -> str:
    lines = [
        "Draw ONE small motif as a single inline SVG. Output ONLY the SVG markup — "
        "no markdown, no prose, no <?xml?> prolog.",
        "Hard rules (a sanitizer rejects any violation):",
        "- The root <svg> MUST have a viewBox; use only <svg>, <g>, <path> elements.",
        "- Vector paths only. NO <image>, <text>, <filter>, gradients, clipPath, "
        "<style>, scripts, or external href.",
        '- Single color: set fill="currentColor" on shapes (the engine binds the real '
        "color). No raw hex fills.",
        "- Center the geometry in the viewBox; keep it simple and recognizable.",
        "",
        f"subject: {spec.get('subject')}",
        f"part: {spec.get('part')}",
    ]
    for k in ("view", "expression", "style", "description"):
        if spec.get(k):
            lines.append(f"{k}: {spec.get(k)}")
    if errors:
        lines += ["", "Your previous SVG was rejected. Fix exactly these:"]
        lines += [f"- {e}" for e in errors]
    return "\n".join(lines)


def generate_motif_svg(
    spec: dict,
    *,
    client: LLMClient | None = None,
    embedding: list[float] | None = None,
    use_cache: bool = True,
) -> str:
    """Generate, sanitize, and register a single-color motif SVG for a spec (miss path).

    Deterministic: the same spec freezes to the same SVG, so the content-hash
    ``motif_id`` is stable. Tier-1 gate (spec §6.4): on a sanitize/structure failure
    the model is re-prompted once; a second failure (or no client) raises
    :class:`AdapterClientError` (the route maps that to 502). Persistence is the
    best-effort write-through inside :func:`register_motif` (never raises here).

    ``embedding`` (S11) is the descriptor vector the resolver already computed for the
    miss; it is persisted with the motif so later requests can soft-match it.
    """
    key = cache_key({"k": "motif_svg", "spec": _canonical_spec(spec)})
    if use_cache and key in _motif_svg_cache:
        return _motif_svg_cache[key]

    llm = _resolve_client(client)

    errors: list[str] | None = None
    for _ in range(2):  # initial attempt + one Tier-1 regeneration
        svg = _extract_svg(llm.complete(_build_svg_prompt(spec, errors=errors)))
        try:
            motif = normalize_motif_svg(svg)
        except (SanitizeError, ValueError) as exc:
            errors = [str(exc)]
            continue
        motif_id = register_motif(
            motif,
            subject=facets.normalize_facet(spec.get("subject")) or None,
            part=facets.normalize_facet(spec.get("part")) or None,
            view=spec.get("view"),
            expression=spec.get("expression"),
            style=spec.get("style"),
            description=spec.get("description"),
            source="llm",
            color_slots=["s0"],
            embedding=embedding,
        )
        if use_cache:
            _motif_svg_cache[key] = motif_id
        return motif_id

    raise AdapterClientError(
        f"LLM motif SVG generation failed the sanitize/structure gate after retry: {errors}"
    )
