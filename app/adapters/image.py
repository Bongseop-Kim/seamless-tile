"""Reference-image adapter: ``image -> intent`` JSON.

Reference images are used for MEANING extraction (style / motif / color), not pixel
reproduction (ARCHITECTURE.md "Reference Image 처리 정책"). Two parts:

- **Palette extraction** is done for real, dependency-free, via Pillow median-cut
  (no scikit-learn). 8-16 dominant colors -> palette slots + a default colorway.
- **VLM structure hints** and **vectorization** are injected seams (Protocols),
  mocked in tests. The vectorize fit/unfit rule (path_count <= N AND color_count <= M)
  decides ``source_fidelity``; unfit textures fall back to palette + a library motif
  and are flagged. Actual raster-hybrid baking is session 8.

Transport for session 7 is a base64 / data-URI string in the JSON body. A minimal
size guard is applied here; full upload validation (format/pixel caps, metadata
strip, multipart) is session 8.
"""

from __future__ import annotations

import base64
import binascii
import copy
import hashlib
import io
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from PIL import Image

from app.adapters.base import AdapterClientError, AdapterResult, cache_key
from app.motifs.registry import MOTIFS
from app.validate.intent import IntentInvalid, validate_intent

DEFAULT_TILE_MM = 48.0
DEFAULT_DPI = 300
DEFAULT_NUM_COLORS = 8
# DoS guards: bound the ENCODED string before allocating the decoded copy (base64
# expands ~4/3), plus a post-decode cap. Full upload hardening (format/pixel caps,
# metadata strip, multipart) is session 8.
MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_ENCODED_CHARS = MAX_IMAGE_BYTES * 4 // 3 + 16
# Vectorization fit thresholds (ARCHITECTURE.md: clean path under a count, bounded colors).
VECTORIZE_MAX_PATHS = 1500
VECTORIZE_MAX_COLORS = 32

_FALLBACK_MOTIF = "circle"  # always present in the registry


@dataclass(frozen=True)
class VectorResult:
    """Contract returned by a vectorizer seam. We judge fitness off these counts;
    we never read the raw pixels for the decision."""

    path_count: int
    color_count: int
    symbol_svg: str | None = None


@runtime_checkable
class VLMClient(Protocol):
    def describe(self, image_bytes: bytes) -> dict: ...


@runtime_checkable
class Vectorizer(Protocol):
    def trace(self, image_bytes: bytes) -> VectorResult: ...


class ImageAdapterError(AdapterClientError):
    """An injected image dependency (VLM / vectorizer) failed."""


_intent_cache: dict[str, dict] = {}


def clear_intent_cache() -> None:
    _intent_cache.clear()


def _decode_image(image_b64: str) -> bytes:
    if len(image_b64) > MAX_ENCODED_CHARS:
        raise IntentInvalid([f"reference_image exceeds the {MAX_IMAGE_BYTES}-byte cap"])
    if image_b64.startswith("data:"):
        # partition never raises (unlike split(...)[1]) when the comma is absent.
        _, _, payload = image_b64.partition(",")
        if not payload:
            raise IntentInvalid(["reference_image data URI has no base64 payload"])
    else:
        payload = image_b64
    try:
        data = base64.b64decode(payload, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise IntentInvalid([f"reference_image is not valid base64: {exc}"]) from None
    if not data:
        raise IntentInvalid(["reference_image is empty"])
    if len(data) > MAX_IMAGE_BYTES:
        raise IntentInvalid([f"reference_image exceeds {MAX_IMAGE_BYTES} bytes"])
    return data


def extract_palette(image_bytes: bytes, *, num_colors: int = DEFAULT_NUM_COLORS) -> list[dict]:
    """Median-cut a reference image into 2-16 hex color slots, frequency-ordered.

    Deterministic: identical bytes -> identical slots. Padded to at least 2 slots so
    an intent always has a ground + an accent.
    """
    n = max(2, min(int(num_colors), 16))
    try:
        img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    except Exception as exc:  # PIL raises a grab-bag; treat as bad caller input
        raise IntentInvalid([f"reference_image could not be decoded as an image: {exc}"]) from None

    quant = img.quantize(colors=n, method=Image.Quantize.MEDIANCUT)
    flat = quant.getpalette() or []
    counts = sorted(quant.getcolors() or [], key=lambda c: (-c[0], c[1]))

    slots: list[dict] = []
    seen: set[str] = set()
    for _, idx in counts:
        r, g, b = flat[idx * 3], flat[idx * 3 + 1], flat[idx * 3 + 2]
        hexv = f"#{r:02x}{g:02x}{b:02x}"
        if hexv in seen:
            continue
        seen.add(hexv)
        slots.append({"id": f"c{len(slots)}", "hex": hexv})

    for pad in ("#000000", "#ffffff"):
        if len(slots) >= 2:
            break
        if pad not in seen:
            seen.add(pad)
            slots.append({"id": f"c{len(slots)}", "hex": pad})
    return slots


def judge_vectorization(
    result: VectorResult,
    *,
    max_paths: int = VECTORIZE_MAX_PATHS,
    max_colors: int = VECTORIZE_MAX_COLORS,
) -> str:
    """Fit (flat/geometric/simple) -> 'vector'; unfit (photo/painterly) -> 'raster_hybrid'."""
    if result.path_count <= max_paths and result.color_count <= max_colors:
        return "vector"
    return "raster_hybrid"


def _assemble_intent(slots: list[dict], *, motif_id: str | None, tile_mm: float, dpi: int) -> dict:
    ground = slots[0]["id"]
    accent = slots[1]["id"] if len(slots) > 1 else slots[0]["id"]
    mapping = {s["id"]: s["hex"] for s in slots}
    layers: list[dict] = [
        {"id": "ground", "type": "background", "z_order": 0, "params": {"color": ground}},
        {
            "id": "stripe_base",
            "type": "stripe",
            "z_order": 1,
            # period = tile/5 with a 3/4 slope (hypot 5) => tile = period*5 => seamless
            # for ANY tile_mm. spacing = tile/8 divides the tile.
            "params": {
                "angle": -36.87,
                "period_mm": tile_mm / 5,
                "bands": [{"offset_mm": 0, "width_mm": tile_mm / 10, "color": accent}],
            },
        },
    ]
    if motif_id is not None:
        motif_color = slots[2]["id"] if len(slots) > 2 else accent
        layers.append(
            {
                "id": "motif_lane",
                "type": "motif",
                "z_order": 2,
                "params": {"motif_id": motif_id, "size_mm": 1.4, "color": motif_color},
                "placement": {
                    "type": "path_following",
                    "host_layer": "stripe_base",
                    "lane": "center",
                    "spacing_mm": tile_mm / 8,
                    "phase_mm": 0,
                },
            }
        )
    return {
        "intent_version": 1,
        "canvas": {"tile_mm": tile_mm, "dpi": dpi},
        "seed": 0,
        "production": {"method": "digital", "max_colors": max(12, len(slots))},
        "palette": {"slots": slots},
        "colorways": [{"id": "default", "name": "default", "mapping": mapping}],
        "layers": layers,
    }


def build_intent(
    image_b64: str,
    *,
    canvas: dict | None = None,
    num_colors: int = DEFAULT_NUM_COLORS,
    vlm: VLMClient | None = None,
    vectorizer: Vectorizer | None = None,
    use_cache: bool = True,
) -> AdapterResult:
    """Turn a reference image into a validated, frozen intent + source_fidelity.

    On the image path the palette/motif derive from the image itself, so only inputs
    that actually change the output (the bytes, canvas, num_colors) are in scope and in
    the cache key.

    Raises :class:`IntentInvalid` for unusable input (bad base64 / undecodable image /
    an intent that fails stage-0 even after dropping the motif layer).
    """
    data = _decode_image(image_b64)
    key = cache_key(
        {
            "k": "image",
            "img": hashlib.sha256(data).hexdigest(),
            "canvas": canvas,
            "num_colors": num_colors,
        }
    )
    if use_cache and key in _intent_cache:
        c = _intent_cache[key]
        # Hand back independent copies so a mutating caller can't corrupt the freeze.
        return AdapterResult(
            intent=copy.deepcopy(c["intent"]),
            source_fidelity=c["fidelity"],
            warnings=list(c["warnings"]),
        )

    slots = extract_palette(data, num_colors=num_colors)
    warnings: list[str] = []

    # VLM structure hint (style/motif). Optional & mocked; only registry motifs are honored.
    motif_id: str | None = _FALLBACK_MOTIF
    if vlm is not None:
        hints = vlm.describe(data)
        cand = hints.get("motif_id") if isinstance(hints, dict) else None
        # isinstance guard: a misbehaving VLM could return an unhashable value, which
        # would raise TypeError on the dict membership test and escape as a 500.
        if isinstance(cand, str) and cand in MOTIFS:
            motif_id = cand

    # Vectorization fit/unfit -> source_fidelity (vectorizer is mocked in tests).
    source_fidelity = "vector"
    if vectorizer is not None:
        vres = vectorizer.trace(data)
        source_fidelity = judge_vectorization(vres)
        if source_fidelity != "vector":
            warnings.append(
                "reference texture is unfit for clean vectorization "
                f"(paths={vres.path_count}, colors={vres.color_count}); using palette + "
                "library motif fallback (raster baking deferred to session 8)"
            )

    tile_mm = float((canvas or {}).get("tile_mm", DEFAULT_TILE_MM))
    dpi = int((canvas or {}).get("dpi", DEFAULT_DPI))

    raw = _assemble_intent(slots, motif_id=motif_id, tile_mm=tile_mm, dpi=dpi)
    try:
        result = validate_intent(raw, repair=True)
    except IntentInvalid:
        # Constrained retry (the image analog of the LLM re-prompt): drop the motif
        # layer and keep background + stripe. If this still fails, IntentInvalid
        # propagates and the route maps it to 422.
        raw = _assemble_intent(slots, motif_id=None, tile_mm=tile_mm, dpi=dpi)
        result = validate_intent(raw, repair=True)

    frozen = result.intent.model_dump(mode="json")
    warnings += list(result.warnings)
    if use_cache:
        _intent_cache[key] = {
            "intent": frozen,
            "fidelity": source_fidelity,
            "warnings": list(warnings),
        }
    return AdapterResult(
        intent=copy.deepcopy(frozen), source_fidelity=source_fidelity, warnings=list(warnings)
    )
