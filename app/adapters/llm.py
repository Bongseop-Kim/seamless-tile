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
import math
import re
from pathlib import Path
from typing import Protocol

from app.adapters.base import AdapterClientError, AdapterResult, cache_key
from app.core.config import get_settings
from app.motifs import facets
from app.motifs.registry import normalize_motif_svg, register_motif
from app.render.sanitize import SanitizeError
from app.validate.intent import IntentInvalid, validate_intent

DEFAULT_TILE_MM = 48.0
DEFAULT_DPI = 300


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
                "angle": -45.0,
                "period_mm": 33.9411,
                "bands": [{"offset_mm": 0, "width_mm": 14.0, "color": "accent"}],
            },
        },
        {
            "id": "dot_lane",
            "type": "motif",
            "z_order": 2,
            "params": {"motif_id": "dot_lane", "size_mm": 1.4, "color": "accent"},
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


# Structural fields kept when distilling a gallery intent into a best-practice example.
# Everything else (palette/colorways/production/seed, and per-layer color refs) is dropped:
# the examples teach COMPOSITION (band rhythm, placement, sizing), not color or motif art.
_SKELETON_PARAM_KEYS = ("angle", "period_mm", "size_mm")


def _structural_skeleton(intent: dict) -> dict:
    """Distill a resolved gallery intent into a color/motif-free structural example.

    Keeps canvas + each layer's type/z_order/placement and the geometry params (angle,
    period_mm, bands offset/width, size_mm). Drops palette/colorways/production/seed and
    every color reference. Replaces a motif layer's real ``motif_id`` with the layer id
    (the placeholder convention the model is told to follow) so examples never teach it to
    invent registry ids. Pure: returns a fresh dict, never mutates the input."""
    skel: dict = {}
    if isinstance(intent.get("canvas"), dict):
        skel["canvas"] = dict(intent["canvas"])
    layers_out: list[dict] = []
    for layer in intent.get("layers", []) or []:
        if not isinstance(layer, dict):
            continue
        lid = layer.get("id")
        lout: dict = {"id": lid, "type": layer.get("type")}
        if "z_order" in layer:
            lout["z_order"] = layer["z_order"]
        params = layer.get("params")
        if isinstance(params, dict):
            pout: dict = {}
            if "motif_id" in params:  # placeholder = layer id (drops the real registry id)
                pout["motif_id"] = lid
            for k in _SKELETON_PARAM_KEYS:
                if k in params:
                    pout[k] = params[k]
            bands = params.get("bands")
            if isinstance(bands, list):
                pout["bands"] = [
                    {bk: b[bk] for bk in ("offset_mm", "width_mm") if bk in b}
                    for b in bands
                    if isinstance(b, dict)
                ]
            if pout:
                lout["params"] = pout
        if isinstance(layer.get("placement"), dict):
            lout["placement"] = copy.deepcopy(layer["placement"])
        layers_out.append(lout)
    skel["layers"] = layers_out
    return skel


def _load_gallery_skeletons() -> list[dict]:
    """Load gallery/*.json and distill each into a structural skeleton (sorted by filename
    for prompt determinism). Best-effort: skips unreadable/invalid files; returns [] if the
    directory is absent so the app never hard-depends on the gallery."""
    gallery_dir = Path(__file__).resolve().parents[2] / "gallery"
    skeletons: list[dict] = []
    for path in sorted(gallery_dir.glob("*.json")):
        try:
            intent = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(intent, dict):
            skeletons.append(_structural_skeleton(intent))
    return skeletons


# Computed once at import (sorted, deterministic); reused across every prompt build.
_GALLERY_SKELETONS = _load_gallery_skeletons()


def _build_prompt(
    user_prompt: str,
    *,
    canvas: dict | None,
    palette: dict | None,
    errors: list[str] | None,
) -> str:
    target_canvas = canvas or {"tile_mm": DEFAULT_TILE_MM, "dpi": DEFAULT_DPI}
    scope_vocab = ", ".join(sorted(facets.SCOPE_VOCAB))
    example = {
        "designs": [
            {
                "intent": _EXAMPLE_INTENT,
                "motif_specs": [
                    {
                        "layer_id": "dot_lane",
                        "subject": "circle",
                        "scope": "whole",
                        "view": "front",
                        "style": "flat",
                        "description": "small solid dot",
                        "complexity": "simple",
                    }
                ],
            },
        ]
    }
    lines = [
        "You convert a textile pattern description into intent JSON for a seamless "
        "SVG engine. The engine handles all geometry, repetition and seamlessness.",
        'Output ONLY one JSON object with a "designs" array. You MUST return 2 to 4 '
        "GENUINELY DIFFERENT designs (not near-duplicates): vary the motif, layout and "
        "structure — band rhythm and placement — NOT just the color. For example, for a "
        "stripe request: one stripe on a solid ground; one with a different band rhythm; "
        "one with a different motif. "
        'Each entry has two keys "intent" and "motif_specs". No SVG, no coordinates, no '
        "markdown, no prose.",
        "",
        "Valid example (follow the JSON shape; do not copy its pattern unless the "
        "user asked for stripes/dot lanes):",
        json.dumps(example, ensure_ascii=False, indent=2),
        "",
        "Constraints:",
        "- intent.intent_version must be 1.",
        "- For EACH motif layer in intent.layers, set params.motif_id to that layer's "
        "id (a placeholder the resolver replaces) and add a matching motif_specs entry "
        "whose layer_id equals the layer id. Do NOT invent registry ids.",
        "- Each motif_specs entry needs: subject (free text, required — any object, "
        "shape, or abstract idea), scope "
        f"(REQUIRED, one of: {scope_vocab}) — the motif's granularity: 'whole' for the "
        "full subject, 'partial' for a sub-region/detail — optional view/expression/"
        "style, and a short English description used for retrieval.",
        '- Optionally add "complexity": "detailed" for painterly / multi-color motifs '
        '(routed to the Recraft generator), or "simple" for single-color geometric '
        "motifs (the default; routed to the LLM).",
        "- layer params colors reference palette slot ids, never raw hex.",
        "- a colorway with id 'default' is required; its mapping covers every slot.",
        "- period_mm must divide tile_mm; motif placement spacing_mm must divide tile_mm.",
        "- Respect the user's pattern class. For simple polka dots on a solid "
        "background, use a background layer plus a motif layer on lattice placement with "
        'a matching motif_specs entry (subject e.g. "dot"/"circle"); do NOT add stripe '
        "host layers.",
        "- A background layer is a flat solid fill: params has only `color` (a palette "
        "slot id). It carries no texture or motif of its own.",
        "- Placement specs are mandatory: type 'lattice' needs a lattice object with "
        "cell_w_mm and cell_h_mm; type 'scatter' needs a scatter object; type "
        "'path_following' needs host_layer+lane or path plus spacing_mm.",
        "- For stripe prompts, use stripe layers. Diagonal stripes default to -45 deg "
        "with period_mm = tile_mm/sqrt(2) (a couple of bold diagonal stripes per tile); "
        "the engine normalizes the diagonal angle/period for you. For non-diagonal "
        "stripes, use angle 0 or 90 with period_mm dividing tile_mm.",
        "- Stripe band widths and the gaps between them should be VARIED/uneven by "
        "default (guard stripes, thick/thin, ratios like 5:2:2, 3:2:1, 6:1:3); the bands "
        "need not fill the period — leave ground gaps. Make stripes equal-width ONLY if "
        "the user explicitly asks for uniform/even stripes.",
        f"- target canvas: {json.dumps(target_canvas)}.",
    ]
    # Best-practice block is large and identical across requests: keep it in the common
    # prefix (before the variable palette/description tail) so Gemini implicit caching can
    # hit it. See https://ai.google.dev/gemini-api/docs — "put large/common content first".
    if _GALLERY_SKELETONS:
        lines += [
            "",
            "Curated best-practice compositions (study these): emulate the stripe angles "
            "and band rhythm (uneven offset/width ratios), and the motif placement, sizing "
            "and layer composition. These are STRUCTURE ONLY — colors and motif art are "
            "stripped/placeholder; choose your own palette and motif_specs to fit the "
            "description. Keep the output shape of the example above (designs/intent/"
            "motif_specs).",
            json.dumps(_GALLERY_SKELETONS, ensure_ascii=False, indent=2),
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


def _split_designs(raw: dict) -> list[tuple[dict, list[dict]]]:
    """Parse the model output into a list of ``(intent, motif_specs)`` designs.

    Accepts the multi-design wrapper ``{"designs": [{"intent":..., "motif_specs":[...]},
    ...]}``, the legacy single wrapper ``{"intent":..., "motif_specs":[...]}``, or a bare
    intent dict. Detection is unambiguous: a valid ``Intent`` is ``extra="forbid"`` so it
    never carries a top-level ``designs`` (or ``intent``) key.
    """
    designs = raw.get("designs")
    if isinstance(designs, list) and designs:
        out = [_split_intent_and_specs(d) for d in designs if isinstance(d, dict)]
        if out:
            return out
    return [_split_intent_and_specs(raw)]


def _validate_spec_facets(specs: list[dict]) -> list[str]:
    """Validate motif-spec facets against the controlled vocab (M2). ``scope`` is
    controlled (``facets.SCOPE_VOCAB``); ``subject`` is free text but required. Returns
    a list of error strings (empty == valid) fed back into the one re-prompt."""
    errors: list[str] = []
    for i, spec in enumerate(specs):
        layer_id = spec.get("layer_id")
        if not isinstance(layer_id, str) or not layer_id:
            errors.append(f"motif_specs[{i}] missing string 'layer_id'")
        subject = spec.get("subject")
        if not isinstance(subject, str) or not subject.strip():
            errors.append(f"motif_specs[{i}] missing non-empty 'subject'")
        scope = spec.get("scope")
        if not isinstance(scope, str) or not scope.strip():
            errors.append(
                f"motif_specs[{i}] missing 'scope' (one of {sorted(facets.SCOPE_VOCAB)})"
            )
            continue
        for field in ("view", "expression", "style", "description"):
            value = spec.get(field)
            if value is not None and not isinstance(value, str):
                errors.append(f"motif_specs[{i}] field '{field}' must be a string")
        try:
            facets.validate_facets(scope)
        except ValueError as exc:
            errors.append(f"motif_specs[{i}]: {exc}")
    return errors


_STRIPE_AXIS_TOL_DEG = 8.0


def _normalize_stripes(intent_raw: dict, settings) -> None:
    """Prompt-path stripe normalization (in place): force a clearly-diagonal stripe to
    -45° with a fixed small repeat count (period = tile/(k·√2), k = repeats//2), scaling
    bands proportionally — so generated ties show a few bold 45° stripes instead of many
    thin ones. Axis-aligned stripes (within tolerance of 0/90) are left untouched. Only
    the LLM/prompt path calls this; intent-direct/image intents are unaffected."""
    try:
        tile = float(intent_raw["canvas"]["tile_mm"])
        layers = intent_raw["layers"]
    except (KeyError, TypeError, ValueError):
        return
    if not isinstance(layers, list):
        return
    k = max(1, settings.stripe_diagonal_repeats // 2)
    target_period = tile / (k * math.sqrt(2.0))
    for layer in layers:
        if not isinstance(layer, dict) or layer.get("type") != "stripe":
            continue
        params = layer.get("params")
        if not isinstance(params, dict):
            continue
        angle = params.get("angle")
        period = params.get("period_mm")
        bands = params.get("bands")
        if not isinstance(angle, (int, float)) or not period or not isinstance(bands, list):
            continue
        a = abs(angle) % 90.0
        if min(a, 90.0 - a) <= _STRIPE_AXIS_TOL_DEG:
            continue  # axis-aligned (vertical/horizontal): respect the intent
        params["angle"] = -45.0
        scale = target_period / period
        for b in bands:
            if not isinstance(b, dict):
                continue
            if isinstance(b.get("offset_mm"), (int, float)):
                b["offset_mm"] = round(b["offset_mm"] * scale, 6)
            if isinstance(b.get("width_mm"), (int, float)):
                b["width_mm"] = round(b["width_mm"] * scale, 6)
        params["period_mm"] = round(target_period, 6)


def build_intents(
    prompt: str,
    *,
    canvas: dict | None = None,
    palette: dict | None = None,
    client: LLMClient | None = None,
    use_cache: bool = True,
) -> list[AdapterResult]:
    """Turn a text prompt into a list of validated, frozen design intents (+ motif specs).

    The model emits ``{"designs": [{"intent":..., "motif_specs":[...]}, ...]}`` — multiple
    distinct DESIGN interpretations of the prompt. The legacy single wrapper and a bare
    intent are still accepted (yielding a one-element list). Each design is validated
    independently; invalid designs are dropped. A re-prompt happens only if NO design is
    valid (initial attempt + one constrained re-prompt), after which :class:`IntentInvalid`
    is raised (the route maps that to 422). Only the finalized list is cached/frozen.
    """
    key = cache_key({"k": "llm", "prompt": prompt, "canvas": canvas, "palette": palette})
    if use_cache and key in _intent_cache:
        # Hand back independent copies so a mutating caller can't corrupt the freeze.
        return [
            AdapterResult(
                intent=copy.deepcopy(c["intent"]),
                source_fidelity="vector",
                warnings=list(c["warnings"]),
                motif_specs=copy.deepcopy(c["motif_specs"]),
            )
            for c in _intent_cache[key]
        ]

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

        results: list[AdapterResult] = []
        frozen_cache: list[dict] = []
        design_errors: list[str] = []
        for idx, (intent_raw, specs) in enumerate(_split_designs(raw)):
            intent_raw.setdefault("intent_version", 1)
            _normalize_stripes(intent_raw, get_settings())
            facet_errors = _validate_spec_facets(specs)
            if facet_errors:
                design_errors += [f"design[{idx}]: {e}" for e in facet_errors]
                continue
            try:
                result = validate_intent(intent_raw, repair=True)
            except IntentInvalid as exc:
                design_errors += [f"design[{idx}]: {e}" for e in exc.errors]
                continue
            frozen = result.intent.model_dump(mode="json")
            warns = list(result.warnings)
            specs_frozen = [dict(s) for s in specs]
            results.append(
                AdapterResult(
                    intent=frozen,
                    source_fidelity="vector",
                    warnings=list(warns),
                    motif_specs=specs_frozen,
                )
            )
            frozen_cache.append(
                {
                    "intent": copy.deepcopy(frozen),
                    "warnings": warns,
                    "motif_specs": copy.deepcopy(specs_frozen),
                }
            )

        if results:
            if use_cache:
                _intent_cache[key] = frozen_cache
            return results
        # No valid design -> feed the collected errors back into the one re-prompt.
        last_exc = IntentInvalid(design_errors or ["LLM produced no valid design"])
        errors = last_exc.errors[:6]

    assert last_exc is not None  # the loop only exits early via return
    raise last_exc


def build_intent(
    prompt: str,
    *,
    canvas: dict | None = None,
    palette: dict | None = None,
    client: LLMClient | None = None,
    use_cache: bool = True,
) -> AdapterResult:
    """Back-compat single-design wrapper: returns the first design of :func:`build_intents`."""
    return build_intents(
        prompt, canvas=canvas, palette=palette, client=client, use_cache=use_cache
    )[0]


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
        f"scope: {spec.get('scope')}",
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
    ``motif_id`` is stable. Tier-1 gate (spec §6.4/§8): ``sanitize`` + structural
    heuristics (drawable, non-degenerate, bbox aspect ratio, and — when a renderer is
    installed — render-error / bbox-overflow seam). On a failure the model is
    re-prompted once; a second failure (or no client) raises :class:`AdapterClientError`
    (the route maps that to 502). Persistence is the best-effort write-through inside
    :func:`register_motif` (never raises here).

    ``embedding`` (S11) is the descriptor vector the resolver already computed for the
    miss; it is persisted with the motif so later requests can soft-match it.
    """
    key = cache_key({"k": "motif_svg", "spec": facets.canonical_spec(spec)})
    if use_cache and key in _motif_svg_cache:
        return _motif_svg_cache[key]

    llm = _resolve_client(client)
    settings = get_settings()

    errors: list[str] | None = None
    for _ in range(2):  # initial attempt + one Tier-1 regeneration
        svg = _extract_svg(llm.complete(_build_svg_prompt(spec, errors=errors)))
        try:
            motif = normalize_motif_svg(
                svg,
                max_aspect_ratio=settings.motif_max_aspect_ratio,
                edge_seam_tol=settings.motif_edge_seam_tol,
                render_check=settings.motif_render_check,
            )
        except (SanitizeError, ValueError) as exc:
            errors = [str(exc)]
            continue
        motif_id = register_motif(
            motif,
            subject=facets.normalize_facet(spec.get("subject")) or None,
            scope=facets.normalize_facet(spec.get("scope")) or None,
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
