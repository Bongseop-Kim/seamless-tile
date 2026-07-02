"""Reference-image adapter: ``image -> intent`` JSON.

Reference images are used for MEANING extraction (style / motif / color), not pixel
reproduction (ARCHITECTURE.md "Reference Image 처리 정책"). Two parts:

Palette extraction is done for real, dependency-free, via Pillow median-cut
(no scikit-learn). 8-16 dominant colors -> palette slots + a default colorway.

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

from PIL import Image, ImageOps

from app.adapters.base import AdapterResult, cache_key
from app.engine.palette import rgb_to_hex
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
# Recraft /images/vectorize input limits (tighter than the adapter caps above). The API
# rejects images outside these, so the multi-image path screens a motif image against
# them BEFORE the call and surfaces a clear reason instead of an opaque upstream 502.
RECRAFT_VECTORIZE_MAX_BYTES = 5 * 1024 * 1024
RECRAFT_VECTORIZE_MAX_DIM = 4096
RECRAFT_VECTORIZE_MIN_DIM = 256
RECRAFT_VECTORIZE_MAX_PIXELS = 16_000_000

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


def decode_and_clean(image_b64: str) -> bytes:
    """Decode → validate → metadata-strip a base64/data-URI image to clean PNG bytes.

    The public entry the multi-image chat path uses before any image leaves the box (to
    Gemini / Recraft): it runs the same encoded-size guard, format/pixel-bomb caps,
    integrity check, and metadata strip the singular ``reference_image`` path does.
    Raises :class:`IntentInvalid` (a 422 at the route) on any violation.
    """
    data = _decode_image(image_b64)
    _validate_image(data)
    return _strip_metadata(data)


def vectorize_limit_error(data: bytes) -> str | None:
    """Reason string if ``data`` is outside Recraft ``/images/vectorize`` limits, else None.

    The adapter caps (8 MB / 8192px / 24 MP) are looser than Recraft's, so an image that
    passes :func:`_validate_image` can still be rejected by vectorize. Screening here lets
    the resolver drop the motif with a clear warning instead of surfacing a 502.
    """
    if len(data) > RECRAFT_VECTORIZE_MAX_BYTES:
        return f"image exceeds {RECRAFT_VECTORIZE_MAX_BYTES} bytes for vectorization"
    try:
        with Image.open(io.BytesIO(data)) as img:
            w, h = img.size
    except Exception as exc:  # noqa: BLE001 - PIL grab-bag; treat as unusable input
        return f"image could not be read: {exc}"
    if min(w, h) < RECRAFT_VECTORIZE_MIN_DIM:
        return f"image min side {min(w, h)}px is below the {RECRAFT_VECTORIZE_MIN_DIM}px minimum"
    if max(w, h) > RECRAFT_VECTORIZE_MAX_DIM:
        return f"image max side {max(w, h)}px exceeds the {RECRAFT_VECTORIZE_MAX_DIM}px maximum"
    if w * h > RECRAFT_VECTORIZE_MAX_PIXELS:
        return f"image {w}x{h}px exceeds the {RECRAFT_VECTORIZE_MAX_PIXELS}-pixel maximum"
    return None


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
        hexv = rgb_to_hex(r, g, b)
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


def _assemble_intent(slots: list[dict], *, tile_mm: float, dpi: int) -> dict:
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
    use_cache: bool = True,
) -> AdapterResult:
    """Turn a reference image into a validated, frozen intent + source_fidelity.

    On the image path the palette/motif derive from the image itself, so only inputs
    that actually change the output (the bytes, canvas, num_colors) are in scope and in
    the cache key.

    Raises :class:`IntentInvalid` for unusable input (bad base64 / undecodable image /
    an intent that fails stage-0 validation).
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
    # ponytail: motif inference (VLM) / vectorization seams removed until a real
    # client exists — the image path is palette-only, source_fidelity always "vector".
    warnings: list[str] = ["motif inference unavailable/ignored; using palette only"]
    source_fidelity = "vector"

    tile_mm = float((canvas or {}).get("tile_mm", DEFAULT_TILE_MM))
    dpi = int((canvas or {}).get("dpi", DEFAULT_DPI))

    raw = _assemble_intent(slots, tile_mm=tile_mm, dpi=dpi)
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
