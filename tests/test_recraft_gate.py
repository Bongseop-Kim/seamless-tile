"""Session-13 Recraft suitability gate (M1, §6.2) + miss-path generation.

The gate flattens painterly Recraft output to the sanitizer allowlist *before*
``normalize_motif_svg``: gradients -> first-stop solid color, filter/clipPath/mask
dropped, raster rejected, color count capped (deterministic quantization). On a
gate/sanitize failure ``generate_via_recraft`` re-prompts once, then raises
``RecraftError`` (-> 502). All generation is mocked (no network).
"""

import glob
import os

import pytest

from app.adapters import recraft
from app.adapters.recraft import (
    RecraftError,
    _flatten_unsuitable,
    generate_via_recraft,
)
from app.engine.composition import compose
from app.motifs.registry import MOTIFS, get_motif, normalize_motif_svg
from app.render.sanitize import SanitizeError
from app.validate.intent import validate_intent

_CORPUS_DIR = os.path.join(os.path.dirname(__file__), "fixtures", "recraft_samples")
# Empirical floor for the synthetic corpus (spec §12 Y% baseline; tune against a live
# Recraft sample via scripts/measure_recraft_passrate.py before production).
_PASS_THRESHOLD = 0.70


def _svg(inner: str, viewbox: str = "0 0 100 100") -> str:
    return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{viewbox}">{inner}</svg>'


def _spec(**extra) -> dict:
    return {"layer_id": "m", "subject": "pig", "part": "face", **extra}


class _FakeRecraft:
    def __init__(self, svg: str) -> None:
        self._svg = svg
        self.calls = 0

    def generate(self, prompt: str) -> str:
        self.calls += 1
        return self._svg


class _SeqRecraft:
    """Returns canned SVGs in order (last repeats); counts calls to observe retries."""

    def __init__(self, *svgs: str) -> None:
        self._svgs = list(svgs)
        self.calls = 0

    def generate(self, prompt: str) -> str:
        self.calls += 1
        return self._svgs[min(self.calls - 1, len(self._svgs) - 1)]


@pytest.fixture(autouse=True)
def _clean():
    def _purge():
        recraft.clear_motif_cache()
        recraft.clear_recraft_motif_cache()
        recraft.set_default_recraft_client(None)
        for key in [k for k in MOTIFS if k.startswith("recraft-")]:
            del MOTIFS[key]

    _purge()
    yield
    _purge()


# --- gate: flatten / reject -------------------------------------------------


def test_gate_flattens_gradient_to_first_stop_color():
    raw = _svg(
        '<defs><linearGradient id="g"><stop offset="0" stop-color="#3a7"/>'
        '<stop offset="1" stop-color="#fff"/></linearGradient></defs>'
        '<rect x="10" y="10" width="80" height="80" fill="url(#g)"/>'
    )
    flat = _flatten_unsuitable(raw)
    assert "gradient" not in flat.lower()  # gradient def removed
    assert "url(" not in flat  # paint reference resolved
    assert "#3a7" in flat  # representative = first stop color
    motif = normalize_motif_svg(flat)  # must now pass the sanitizer
    assert motif.color_slots == ("s0",)  # one solid color -> single slot


def test_gate_drops_filter_clip_and_mask():
    raw = _svg(
        '<defs><filter id="f"><feGaussianBlur stdDeviation="2"/></filter>'
        '<clipPath id="c"><rect x="0" y="0" width="10" height="10"/></clipPath></defs>'
        '<rect x="10" y="10" width="80" height="80" fill="#abc" '
        'filter="url(#f)" clip-path="url(#c)" mask="url(#m)"/>'
    )
    flat = _flatten_unsuitable(raw)
    assert "filter" not in flat.lower()
    assert "clip" not in flat.lower()
    assert "mask" not in flat.lower()
    motif = normalize_motif_svg(flat)
    assert motif.id.startswith("recraft-")


def test_gate_rejects_raster_image():
    with pytest.raises(ValueError):
        _flatten_unsuitable(_svg('<image href="data:image/png;base64,AAAA"/>'))


def test_gate_returns_clean_svg_unchanged():
    # Determinism contract: a clean SVG is byte-identical through the gate, so the
    # authoring/LLM normalized ids never shift.
    clean = _svg('<circle cx="50" cy="50" r="40" fill="#123456"/>')
    assert _flatten_unsuitable(clean) == clean


def test_gate_resolves_unknown_url_paint_to_none():
    flat = _flatten_unsuitable(
        _svg('<rect x="0" y="0" width="10" height="10" fill="url(#missing)"/>')
    )
    assert 'fill="none"' in flat


# --- quantization (color-slot cap) ------------------------------------------


def test_quantize_caps_color_slots():
    colors = ["#000000", "#111111", "#222222", "#ff0000", "#00ff00", "#0000ff", "#ffffff"]
    seven = _svg(
        "".join(
            f'<rect x="{i * 10}" y="0" width="10" height="100" fill="{c}"/>'
            for i, c in enumerate(colors)
        )
    )
    motif = normalize_motif_svg(seven, max_color_slots=6)
    assert len(motif.color_slots) <= 6


def test_quantize_is_deterministic():
    colors = ["#000000", "#101010", "#202020", "#ff0000", "#00ff00", "#0000ff", "#ffffff"]
    seven = _svg(
        "".join(
            f'<rect x="{i * 10}" y="0" width="10" height="100" fill="{c}"/>'
            for i, c in enumerate(colors)
        )
    )
    a = normalize_motif_svg(seven, max_color_slots=6)
    b = normalize_motif_svg(seven, max_color_slots=6)
    assert a.id == b.id and a.symbol == b.symbol


# --- generate_via_recraft (miss path) ---------------------------------------


def test_generate_via_recraft_registers_with_recraft_source():
    two = _svg(
        '<rect x="0" y="0" width="50" height="100" fill="#ff0000"/>'
        '<rect x="50" y="0" width="50" height="100" fill="#0000ff"/>'
    )
    mid = generate_via_recraft(_spec(), client=_FakeRecraft(two), use_cache=False)
    motif = get_motif(mid)
    assert motif.color_slots == ("s0", "s1")  # multicolor slots preserved


def test_generate_via_recraft_freezes_by_spec():
    client = _FakeRecraft(_svg('<circle cx="50" cy="50" r="40" fill="#abc"/>'))
    a = generate_via_recraft(_spec(), client=client)
    b = generate_via_recraft(_spec(), client=client)  # same spec -> freeze cache hit
    assert a == b
    assert client.calls == 1


def test_generate_via_recraft_retries_once_then_succeeds():
    bad = _svg('<image href="x.png"/>')  # gate rejects -> regenerate
    good = _svg('<rect x="10" y="10" width="80" height="80" fill="#abc"/>')
    client = _SeqRecraft(bad, good)
    mid = generate_via_recraft(_spec(), client=client, use_cache=False)
    assert client.calls == 2
    assert get_motif(mid).id == mid


def test_generate_via_recraft_exhausted_raises_client_error():
    client = _SeqRecraft(_svg('<image href="x.png"/>'), _svg('<image href="y.png"/>'))
    with pytest.raises(RecraftError):
        generate_via_recraft(_spec(), client=client, use_cache=False)
    assert client.calls == 2  # exactly one retry, no more


def test_generate_via_recraft_unconfigured_raises():
    with pytest.raises(RecraftError):
        generate_via_recraft(_spec(), client=None, use_cache=False)


# --- acceptance #1: detailed -> multicolor Recraft motif composes ------------


def _multicolor_intent(motif_id: str, slots: dict[str, str]) -> dict:
    return {
        "intent_version": 1,
        "canvas": {"tile_mm": 48, "dpi": 300},
        "seed": 7,
        "production": {"method": "digital", "max_colors": 12},
        "palette": {"slots": [{"id": s, "hex": h} for s, h in slots.items()]},
        "colorways": [{"id": "default", "mapping": dict(slots)}],
        "layers": [
            {"id": "bg", "type": "background", "z_order": 0,
             "params": {"color": list(slots)[0]}},
            {"id": "shape", "type": "motif", "z_order": 1,
             "params": {"motif_id": motif_id, "size_mm": 10.0,
                        "colors": {"s0": "p0", "s1": "p1"}},
             "placement": {"type": "lattice",
                           "lattice": {"cell_w_mm": 12, "cell_h_mm": 12}}},
        ],
    }


def test_recraft_multicolor_motif_composes_with_slot_binding():
    two = _svg(
        '<rect x="0" y="0" width="50" height="100" fill="#ff0000"/>'
        '<rect x="50" y="0" width="50" height="100" fill="#0000ff"/>'
    )
    mid = generate_via_recraft(_spec(), client=_FakeRecraft(two), use_cache=False)
    raw = _multicolor_intent(mid, {"p0": "#00aa00", "p1": "#aa00aa"})
    result = validate_intent(raw)
    svg = compose(result.intent, result.palette, "default")
    assert f"motif-{mid}-s0" in svg and f"motif-{mid}-s1" in svg  # per-slot symbols
    assert "#00aa00" in svg and "#aa00aa" in svg  # each slot bound to its palette color


# --- single-object correction: background removal + tight-bbox framing ------


def test_gate_removes_full_canvas_background():
    bg = '<path d="M0 0 L100 0 L100 100 L0 100 Z" fill="rgb(237,237,228)"/>'  # full canvas
    obj = '<circle cx="50" cy="50" r="20" fill="rgb(255,0,0)"/>'
    flat = _flatten_unsuitable(_svg(bg + obj))
    assert "<path" not in flat  # the full-canvas background path is dropped
    assert "circle" in flat  # the actual object is kept
    assert "edede4" not in flat.lower()  # rgb(237,237,228) -> #edede4 background color gone


def test_gate_keeps_single_full_frame_shape():
    # A motif that IS one full-frame shape must NOT be stripped (background guard).
    flat = _flatten_unsuitable(_svg('<rect x="0" y="0" width="100" height="100" fill="rgb(0,0,0)"/>'))
    assert "rect" in flat
    assert "#000000" in flat


def test_normalize_tight_bbox_frames_small_object():
    # circle r=10 centered in a 100x100 viewBox: tight bbox extent 20 -> scale 1/20 = 0.05,
    # so the object fills the unit box (vs the old viewBox framing's scale 1/100 = 0.01).
    motif = normalize_motif_svg(_svg('<circle cx="50" cy="50" r="10" fill="#abc"/>'))
    assert "scale(0.05)" in motif.symbol
    assert "scale(0.01)" not in motif.symbol


def test_gate_then_normalize_yields_single_object_motif():
    # Background dropped before normalization -> only the object's single color survives.
    bg = '<path d="M0 0 L100 0 L100 100 L0 100 Z" fill="rgb(10,20,30)"/>'
    obj = '<rect x="40" y="40" width="20" height="20" fill="rgb(200,0,0)"/>'
    motif = normalize_motif_svg(_flatten_unsuitable(_svg(bg + obj)), max_color_slots=6)
    assert motif.color_slots == ("s0",)  # background color gone, single object color remains


# --- acceptance #4: corpus sanitize pass-rate -------------------------------


def test_corpus_pass_rate_meets_threshold():
    paths = sorted(glob.glob(os.path.join(_CORPUS_DIR, "*.svg")))
    assert paths, "no Recraft corpus fixtures found"
    passed = 0
    for path in paths:
        with open(path, encoding="utf-8") as fh:
            raw = fh.read()
        try:
            normalize_motif_svg(_flatten_unsuitable(raw), max_color_slots=6)
            passed += 1
        except (SanitizeError, ValueError):
            pass
    rate = passed / len(paths)
    assert rate >= _PASS_THRESHOLD, f"gate pass rate {rate:.2f} < {_PASS_THRESHOLD}"
