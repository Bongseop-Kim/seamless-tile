"""Reference-image adapter: ``image -> intent`` JSON.

Reference images are used for MEANING extraction (style / motif / color), not pixel
reproduction (ARCHITECTURE.md "Reference Image 처리 정책"). Two parts:

- **Palette extraction** is done for real, dependency-free, via Pillow median-cut
  (no scikit-learn). 8-16 dominant colors -> palette slots + a default colorway.
- **VLM structure hints** and **vectorization** are planned injected seams (Protocols).
  They are not wired to real clients in the FastAPI route yet; tests pin the intended
  contract while the reference-image product flow is still being planned.
  The vectorize fit/unfit rule (path_count <= N AND color_count <= M) decides
  ``source_fidelity``; unfit textures fall back to palette only and are flagged.

Transport is a base64 / data-URI string in the JSON body. Upload validation runs on
this path (session 8): an encoded-size guard, then format allowlist, pixel/decode-bomb
caps, an integrity check, and a metadata strip before palette extraction.
"""

from __future__ import annotations

import base64
import binascii
import copy
import hashlib
import io
from dataclasses import dataclass
from typing import Protocol

from PIL import Image, ImageOps

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
# Upload hardening: allow only real raster formats; a format-spoofed payload (e.g. an
# SVG/HTML polyglot claiming image/png) sniffs to a disallowed format and is rejected.
# The pixel-count cap bounds decode work — a decompression bomb's huge *declared*
# dimensions are read from the header and rejected before any pixels decode.
ALLOWED_IMAGE_FORMATS = frozenset({"PNG", "JPEG", "WEBP"})
MAX_IMAGE_DIM = 8192
MAX_IMAGE_PIXELS = 24_000_000
# Vectorization fit thresholds (ARCHITECTURE.md: clean path under a count, bounded colors).
VECTORIZE_MAX_PATHS = 1500
VECTORIZE_MAX_COLORS = 32

@dataclass(frozen=True)
class VectorResult:
    """Contract returned by a vectorizer seam. We judge fitness off these counts;
    we never read the raw pixels for the decision."""

    path_count: int
    color_count: int
    symbol_svg: str | None = None


class VLMClient(Protocol):
    def describe(self, image_bytes: bytes) -> dict: ...


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


def _validate_image(data: bytes) -> None:
    """Reject disallowed formats, oversize/decode-bomb dimensions, and corrupt streams.

    ``Image.open`` reads only the header (it is lazy), so the dimension caps are
    enforced before any pixels are decoded; ``verify()`` then walks the full stream to
    catch truncation and format spoofs. Raises :class:`IntentInvalid` (a 422 at the
    route) on any violation.
    """
    try:
        img = Image.open(io.BytesIO(data))
        fmt = img.format
        width, height = img.size
    except Exception as exc:  # noqa: BLE001 - PIL raises a grab-bag; treat as caller input
        raise IntentInvalid([f"reference_image could not be decoded as an image: {exc}"]) from None
    if fmt not in ALLOWED_IMAGE_FORMATS:
        raise IntentInvalid(
            [f"reference_image format {fmt!r} not allowed; use one of {sorted(ALLOWED_IMAGE_FORMATS)}"]
        )
    if width <= 0 or height <= 0 or width > MAX_IMAGE_DIM or height > MAX_IMAGE_DIM:
        raise IntentInvalid(
            [f"reference_image {width}x{height}px exceeds the {MAX_IMAGE_DIM}px per-side cap"]
        )
    if width * height > MAX_IMAGE_PIXELS:
        raise IntentInvalid(
            [f"reference_image {width}x{height}px exceeds the {MAX_IMAGE_PIXELS}-pixel cap"]
        )
    try:
        Image.open(io.BytesIO(data)).verify()  # verify() consumes the image; reopen to use
    except Exception as exc:  # noqa: BLE001 - verify() may raise varied PIL exceptions
        raise IntentInvalid([f"reference_image failed integrity check: {exc}"]) from None


def _strip_metadata(data: bytes) -> bytes:
    """Return clean PNG bytes carrying only RGB pixels — no EXIF/ICC/text chunks.

    Copying the pixel data into a fresh image drops the source ``.info`` dict, and the
    re-encode discards any appended polyglot payload. Lossless, so palette extraction
    is unaffected.
    """
    # Apply (then discard) EXIF orientation so the stripped image is upright, then
    # rebuild from the raw pixel buffer so no source ``.info`` (EXIF/ICC/text) carries
    # over and the re-encode drops any appended polyglot payload.
    src = ImageOps.exif_transpose(Image.open(io.BytesIO(data))).convert("RGB")
    clean = Image.frombytes("RGB", src.size, src.tobytes())
    buf = io.BytesIO()
    clean.save(buf, format="PNG")
    return buf.getvalue()


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


def judge_vectorization(result: VectorResult) -> str:
    """Fit (flat/geometric/simple) -> 'vector'; unfit (photo/painterly) -> 'raster_hybrid'."""
    if result.path_count <= VECTORIZE_MAX_PATHS and result.color_count <= VECTORIZE_MAX_COLORS:
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
        "production": {"method": "print", "max_colors": max(12, len(slots))},
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
    # Session-8 upload hardening: reject spoofed/oversize/corrupt images, then strip
    # metadata so only RGB pixels flow downstream (and into the freeze-cache key).
    _validate_image(data)
    data = _strip_metadata(data)
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
    # No usable hint -> motif_id stays None and the motif layer is dropped (palette only).
    motif_id: str | None = None
    if vlm is not None:
        try:
            hints = vlm.describe(data)
        except Exception as exc:  # noqa: BLE001 - external client failures map to 502
            raise ImageAdapterError(f"VLM service failed while describing image: {exc}") from exc
        cand = hints.get("motif_id") if isinstance(hints, dict) else None
        # isinstance guard: a misbehaving VLM could return an unhashable value, which
        # would raise TypeError on the dict membership test and escape as a 500.
        if isinstance(cand, str) and cand in MOTIFS:
            motif_id = cand
    if motif_id is None:
        warnings.append("motif inference unavailable/ignored; using palette only")

    # Vectorization fit/unfit -> source_fidelity (vectorizer is mocked in tests).
    source_fidelity = "vector"
    if vectorizer is not None:
        try:
            vres = vectorizer.trace(data)
        except Exception as exc:  # noqa: BLE001 - external client failures map to 502
            raise ImageAdapterError(f"vectorizer service failed while tracing image: {exc}") from exc
        source_fidelity = judge_vectorization(vres)
        if source_fidelity != "vector":
            warnings.append(
                "reference texture is unfit for clean vectorization "
                f"(paths={vres.path_count}, colors={vres.color_count}); using palette "
                "only (raster baking deferred to session 8)"
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
