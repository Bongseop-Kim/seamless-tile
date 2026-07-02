"""Edit-tool whitelist + deterministic apply (spec §7, S2/S3/S10).

The LLM may only *select* tools from this closed set (bound via ``TOOL_SCHEMAS``); it
never rewrites the intent as free JSON. ``apply_tools`` applies each selected tool as a
**pure Python patch** — no clock, no randomness, no dict-order dependence (CLAUDE.md
determinism rule, acceptance #4) — reusing the semantic checks in ``app.validate.intent``
as the final backstop. Anything outside this whitelist is dropped, and a structurally
invalid result is rejected by ``validate_intent`` downstream (acceptance #2).

A few tools change **session state**, not the engine intent, on purpose:
- ``set_colorway``/``set_seed`` set the compose parameters (the engine takes colorway/seed
  alongside the intent), so they live on the session, keeping the committed intent stable.
- ``set_material`` writes a material map consumed only by the finalize (fabric-texture)
  stage; the engine is material-agnostic, so this must never touch the intent (spec §7).

``swap_motif`` and ``add_layer`` (of a motif) do NOT resolve a motif here — they emit a
spec for the confirm gate (spec §8.3, S11/S12). Recraft is never called from this module.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.engine.intent import Intent

_HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")

# Sentinel motif id parked on a layer whose real motif is still pending the gate; the
# resolve gate overwrites it with a concrete motif_id before validate/commit ever run.
PENDING_MOTIF = "__pending__"

# The closed set of tool names the LLM may call (spec §7). Exposed for enforcement and
# for building the bind_tools schemas.
TOOL_NAMES = (
    "set_colorway",
    "set_palette_slot",
    "scale_motif",
    "set_stripe",
    "set_density",
    "add_layer",
    "remove_layer",
    "swap_motif",
    "set_seed",
    "regenerate",
    "set_material",
)


class ToolArgError(Exception):
    """A tool call whose arguments cannot be applied (unknown id, bad value). The turn
    keeps going (the tool is skipped with a warning); it is not a crash."""


@dataclass
class ToolOutcome:
    """Result of applying a turn's tool calls to the working intent."""

    intent: dict
    motif_specs: list[dict] = field(default_factory=list)  # need the resolve gate
    state_updates: dict = field(default_factory=dict)  # colorway/seed/material_map merge
    warnings: list[str] = field(default_factory=list)


# --- helpers ------------------------------------------------------------------


def _layer_index(intent: Intent, layer_id: str, *, want_type: str | None = None) -> int:
    for i, layer in enumerate(intent.layers):
        if layer.id == layer_id:
            if want_type is not None and layer.type != want_type:
                raise ToolArgError(
                    f"layer {layer_id!r} is a {layer.type}, not a {want_type}"
                )
            return i
    raise ToolArgError(f"unknown layer_id {layer_id!r}")


def _replace_layer(intent: Intent, index: int, new_layer) -> Intent:
    layers = list(intent.layers)
    layers[index] = new_layer
    return intent.model_copy(update={"layers": layers})


def _require(args: dict, key: str):
    if key not in args or args[key] is None:
        raise ToolArgError(f"missing required arg {key!r}")
    return args[key]


# --- intent-patching tools ----------------------------------------------------


def _set_palette_slot(intent: Intent, args: dict) -> Intent:
    slot_id = _require(args, "slot_id")
    hex_value = str(_require(args, "hex"))
    if not _HEX_RE.match(hex_value):
        raise ToolArgError(f"hex {hex_value!r} must be #RRGGBB")
    slots = list(intent.palette.slots)
    idx = next((i for i, s in enumerate(slots) if s.id == slot_id), None)
    if idx is None:
        raise ToolArgError(f"unknown palette slot_id {slot_id!r}")
    slots[idx] = slots[idx].model_copy(update={"hex": hex_value})
    # The engine resolves a slot's rendered color through the colorway mapping, not the
    # slot's base hex — so update every colorway that maps this slot, or the edit would be
    # invisible. Colorways that don't map the slot are untouched.
    colorways = []
    for cw in intent.colorways:
        if slot_id in cw.mapping:
            colorways.append(
                cw.model_copy(update={"mapping": {**cw.mapping, slot_id: hex_value}})
            )
        else:
            colorways.append(cw)
    return intent.model_copy(
        update={
            "palette": intent.palette.model_copy(update={"slots": slots}),
            "colorways": colorways,
        }
    )


def _scale_motif(intent: Intent, args: dict) -> Intent:
    layer_id = _require(args, "layer_id")
    factor = float(_require(args, "factor"))
    if factor <= 0:
        raise ToolArgError(f"factor must be positive, got {factor}")
    idx = _layer_index(intent, layer_id, want_type="motif")
    layer = intent.layers[idx]
    new_size = round(layer.params.size_mm * factor, 6)
    new_layer = layer.model_copy(
        update={"params": layer.params.model_copy(update={"size_mm": new_size})}
    )
    return _replace_layer(intent, idx, new_layer)


def _set_stripe(intent: Intent, args: dict) -> Intent:
    layer_id = _require(args, "layer_id")
    idx = _layer_index(intent, layer_id, want_type="stripe")
    layer = intent.layers[idx]
    update: dict = {}
    if args.get("angle") is not None:
        update["angle"] = float(args["angle"])
    if args.get("period_mm") is not None:
        period = float(args["period_mm"])
        if period <= 0:
            raise ToolArgError(f"period_mm must be positive, got {period}")
        update["period_mm"] = period
    if not update:
        raise ToolArgError("set_stripe needs at least one of angle, period_mm")
    new_layer = layer.model_copy(
        update={"params": layer.params.model_copy(update=update)}
    )
    return _replace_layer(intent, idx, new_layer)


def _set_density(intent: Intent, args: dict) -> Intent:
    layer_id = _require(args, "layer_id")
    spacing = args.get("spacing_mm")
    if spacing is None:
        # ponytail: P0 supports spacing_mm only (the common path_following/scatter knob);
        # count->spacing conversion needs the lane closure — add when a test needs it.
        raise ToolArgError("set_density requires spacing_mm (count is not supported yet)")
    spacing = float(spacing)
    if spacing <= 0:
        raise ToolArgError(f"spacing_mm must be positive, got {spacing}")
    idx = _layer_index(intent, layer_id, want_type="motif")
    layer = intent.layers[idx]
    placement = layer.placement
    if placement is None:
        raise ToolArgError(f"layer {layer_id!r} has no placement to set density on")
    if placement.type == "path_following":
        new_placement = placement.model_copy(update={"spacing_mm": spacing})
    elif placement.type == "scatter" and placement.scatter is not None:
        new_placement = placement.model_copy(
            update={"scatter": placement.scatter.model_copy(update={"min_dist_mm": spacing})}
        )
    else:
        raise ToolArgError(
            f"density not adjustable for {placement.type!r} placement via spacing_mm"
        )
    return _replace_layer(intent, idx, layer.model_copy(update={"placement": new_placement}))


def _remove_layer(intent: Intent, args: dict) -> Intent:
    layer_id = _require(args, "layer_id")
    idx = _layer_index(intent, layer_id)
    layers = [la for i, la in enumerate(intent.layers) if i != idx]
    if not layers:
        raise ToolArgError("cannot remove the last layer")
    return intent.model_copy(update={"layers": layers})


def _add_layer(intent: Intent, args: dict) -> tuple[Intent, dict | None]:
    """Append a layer. If it is a motif layer without a concrete motif_id, park a pending
    sentinel and return a spec for the resolve gate (Recraft is never called here)."""
    spec = _require(args, "layer")
    if not isinstance(spec, dict) or "id" not in spec or "type" not in spec:
        raise ToolArgError("add_layer.layer must be a layer dict with id and type")
    new_id = spec["id"]
    if any(la.id == new_id for la in intent.layers):
        raise ToolArgError(f"layer id {new_id!r} already exists")
    spec = dict(spec)
    spec.setdefault("z_order", max((la.z_order for la in intent.layers), default=0) + 1)
    motif_spec: dict | None = None
    if spec.get("type") == "motif":
        params = dict(spec.get("params") or {})
        if not params.get("motif_id"):
            params["motif_id"] = PENDING_MOTIF
            params.setdefault("color", "s0")  # placeholder; gate + validate settle it
            spec["params"] = params
            facets = args.get("motif") or {}
            motif_spec = {"layer_id": new_id, **facets, "force_new": bool(args.get("force_new"))}
    # Rebuild via the whole-intent model so the discriminated Layer union validates the
    # new layer's shape now (a malformed layer raises here, not deep in compose).
    raw = intent.model_dump(mode="json")
    raw["layers"].append(spec)
    new_intent = Intent.model_validate(raw)
    return new_intent, motif_spec


# --- session-state tools (do NOT touch the engine intent) ---------------------


def _set_colorway(intent: Intent, args: dict) -> str:
    colorway_id = _require(args, "colorway_id")
    if colorway_id not in {c.id for c in intent.colorways}:
        raise ToolArgError(f"unknown colorway_id {colorway_id!r}")
    return colorway_id


def _set_seed(args: dict) -> int:
    seed = _require(args, "seed")
    try:
        return int(seed)
    except (TypeError, ValueError):
        raise ToolArgError(f"seed must be an int, got {seed!r}") from None


def _set_material(intent: Intent, args: dict) -> dict:
    target = _require(args, "target")
    valid = {la.id for la in intent.layers} | {s.id for s in intent.palette.slots}
    if target not in valid:
        raise ToolArgError(f"set_material target {target!r} is not a layer or slot id")
    material = {
        k: args[k] for k in ("fabric", "finish", "lighting") if args.get(k) is not None
    }
    if not material:
        raise ToolArgError("set_material needs at least one of fabric, finish, lighting")
    return {target: material}


# --- swap_motif (gate only) ---------------------------------------------------


def _swap_motif(intent: Intent, args: dict) -> dict:
    """Record a motif-change spec for the resolve gate. Does NOT change the intent's
    motif_id (the gate freezes the new one) and NEVER calls Recraft (S11/S12)."""
    layer_id = _require(args, "layer_id")
    _layer_index(intent, layer_id, want_type="motif")  # existence/type check
    description = _require(args, "description")
    return {
        "layer_id": layer_id,
        "description": str(description),
        "subject": args.get("subject"),
        "scope": args.get("scope") or "whole",
        "prefer_reuse": bool(args.get("prefer_reuse", True)),
        "force_new": bool(args.get("force_new", False)),
    }


# --- dispatch -----------------------------------------------------------------


def apply_tools(intent_raw: dict, tool_calls: list[dict]) -> ToolOutcome:
    """Apply an ordered list of ``{"name", "args"}`` tool calls to ``intent_raw``.

    Pure function of ``(intent_raw, tool_calls)``: same input → same ``ToolOutcome``
    (acceptance #4). Unknown tool names and un-appliable args are skipped with a warning
    (whitelist enforcement); the committed intent is still run through
    ``validate_intent`` downstream as the structural backstop.
    """
    intent = Intent.model_validate(intent_raw)
    out = ToolOutcome(intent=intent_raw)
    material_map: dict = {}

    for call in tool_calls:
        name = call.get("name")
        args = call.get("args") or {}
        if name not in TOOL_NAMES:
            out.warnings.append(f"tool {name!r} is not in the edit whitelist; ignored")
            continue
        try:
            if name == "set_palette_slot":
                intent = _set_palette_slot(intent, args)
            elif name == "scale_motif":
                intent = _scale_motif(intent, args)
            elif name == "set_stripe":
                intent = _set_stripe(intent, args)
            elif name == "set_density":
                intent = _set_density(intent, args)
            elif name == "remove_layer":
                intent = _remove_layer(intent, args)
            elif name == "add_layer":
                intent, motif_spec = _add_layer(intent, args)
                if motif_spec is not None:
                    out.motif_specs.append(motif_spec)
            elif name == "swap_motif":
                out.motif_specs.append(_swap_motif(intent, args))
            elif name == "set_colorway":
                out.state_updates["colorway"] = _set_colorway(intent, args)
            elif name == "set_seed":
                out.state_updates["seed"] = _set_seed(args)
            elif name == "set_material":
                material_map.update(_set_material(intent, args))
            elif name == "regenerate":
                pass  # valid no-op: commit re-emits candidates every turn anyway
        except ToolArgError as exc:
            out.warnings.append(f"tool {name!r} skipped: {exc}")

    if material_map:
        out.state_updates["material_map"] = material_map
    out.intent = intent.model_dump(mode="json")
    return out
