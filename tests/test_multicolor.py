"""Session-12 multicolor engine: slot preservation (normalize), per-slot render
symbols, instance-level color binding (compose, method (b)), colors<->color_slots
validation, dedup/colorway-agnostic symbols, and byte determinism.

Single-color motifs stay on the legacy ``currentColor`` path (backward compat); the
in-memory ``MOTIFS`` dict is purged of test-authored ``recraft-`` motifs around every
test so global state never leaks into other suites.
"""

import io
import xml.etree.ElementTree as ET

import pytest
from PIL import Image

from app.engine.composition import compose
from app.motifs.registry import (
    MOTIFS,
    normalize_motif_svg,
    register_motif,
    slot_render_symbols,
)
from app.render.raster import find_renderer, rasterize
from app.validate.intent import IntentInvalid, validate_intent

NS = "{http://www.w3.org/2000/svg}"


from tests._helpers import _svg


# Left half red, right half blue -> 2 distinct colors -> slots ("s0", "s1").
_TWO_COLOR = _svg(
    '<rect x="0" y="0" width="50" height="100" fill="#ff0000"/>'
    '<rect x="50" y="0" width="50" height="100" fill="#0000ff"/>'
)
# Three vertical bands -> slots ("s0", "s1", "s2") in DFS first-appearance order.
_THREE_COLOR = _svg(
    '<rect x="0" y="0" width="34" height="100" fill="#ff0000"/>'
    '<rect x="34" y="0" width="33" height="100" fill="#00ff00"/>'
    '<rect x="67" y="0" width="33" height="100" fill="#0000ff"/>'
)


def _palette(slots: dict[str, str], extra_colorways: dict[str, dict] | None = None):
    colorways = [{"id": "default", "mapping": dict(slots)}]
    if extra_colorways:
        for cid, mapping in extra_colorways.items():
            colorways.append({"id": cid, "mapping": mapping})
    return (
        [{"id": sid, "hex": hex_} for sid, hex_ in slots.items()],
        colorways,
    )


def _intent(motif_id: str, params: dict, slots: dict[str, str], extra_cw=None) -> dict:
    palette_slots, colorways = _palette(slots, extra_cw)
    return {
        "intent_version": 1,
        "canvas": {"tile_mm": 48, "dpi": 300},
        "seed": 7,
        "production": {"method": "digital", "max_colors": 12},
        "palette": {"slots": palette_slots},
        "colorways": colorways,
        "layers": [
            {"id": "bg", "type": "background", "z_order": 0,
             "params": {"color": list(slots)[0]}},
            {
                "id": "shape",
                "type": "motif",
                "z_order": 1,
                "params": {"motif_id": motif_id, "size_mm": 10.0, **params},
                "placement": {
                    "type": "lattice",
                    "lattice": {"cell_w_mm": 12, "cell_h_mm": 12},
                },
            },
        ],
    }


# --- normalize: slot extraction ----------------------------------------------


def test_single_color_keeps_currentcolor_and_one_slot():
    motif = normalize_motif_svg(_svg('<circle cx="50" cy="50" r="40" fill="#abc"/>'))
    assert motif.color_slots == ("s0",)
    assert "currentColor" in motif.symbol
    assert 's0"' not in motif.symbol  # no slot token in single-color symbol


def test_two_colors_become_two_slots_in_dfs_order():
    motif = normalize_motif_svg(_TWO_COLOR)
    assert motif.color_slots == ("s0", "s1")
    assert 'fill="s0"' in motif.symbol
    assert 'fill="s1"' in motif.symbol
    assert motif.symbol.index('fill="s0"') < motif.symbol.index('fill="s1"')


def test_three_colors_become_three_slots():
    motif = normalize_motif_svg(_THREE_COLOR)
    assert motif.color_slots == ("s0", "s1", "s2")
    for tok in ("s0", "s1", "s2"):
        assert f'fill="{tok}"' in motif.symbol


def test_shared_color_across_fill_and_stroke_is_one_slot():
    motif = normalize_motif_svg(
        _svg('<rect x="10" y="10" width="80" height="80" fill="#f00" stroke="#f00"/>')
    )
    assert motif.color_slots == ("s0",)  # single distinct color


def test_fill_and_stroke_distinct_colors_are_two_slots():
    motif = normalize_motif_svg(
        _svg('<rect x="10" y="10" width="80" height="80" fill="#f00" stroke="#00f"/>')
    )
    assert motif.color_slots == ("s0", "s1")
    assert 'fill="s0"' in motif.symbol
    assert 'stroke="s1"' in motif.symbol


def test_slot_id_is_colorway_agnostic_and_stable():
    # Same geometry hashes to the same id regardless of how many times normalized.
    a = normalize_motif_svg(_TWO_COLOR)
    b = normalize_motif_svg(_TWO_COLOR)
    assert a.id == b.id


def test_pure_currentcolor_source_stays_single_color():
    motif = normalize_motif_svg(
        _svg('<circle cx="50" cy="50" r="40" fill="currentColor"/>')
    )
    assert motif.color_slots == ("s0",)
    assert "currentColor" in motif.symbol


def test_currentcolor_mixed_with_concrete_is_promoted_to_a_slot():
    # Contract pin (review): currentColor is not a magic inherited paint in a
    # multicolor motif — it becomes its own explicit slot token, so no bare
    # currentColor survives into the slotified symbol (it would otherwise leak into
    # every per-slot overlay).
    motif = normalize_motif_svg(
        _svg(
            '<rect x="0" y="0" width="34" height="100" fill="currentColor"/>'
            '<rect x="34" y="0" width="33" height="100" fill="#00ff00"/>'
            '<rect x="67" y="0" width="33" height="100" fill="#0000ff"/>'
        )
    )
    assert motif.color_slots == ("s0", "s1", "s2")
    assert "currentColor" not in motif.symbol  # promoted to token s0, not left bare
    for tok in ("s0", "s1", "s2"):
        assert f'fill="{tok}"' in motif.symbol


# --- slot_render_symbols ------------------------------------------------------


def test_render_symbols_single_color_unchanged():
    motif = normalize_motif_svg(_svg('<circle cx="50" cy="50" r="40" fill="#abc"/>'))
    syms = slot_render_symbols(motif)
    assert syms == [(f"motif-{motif.id}", motif.symbol)]


def test_render_symbols_multicolor_expands_per_slot():
    motif = normalize_motif_svg(_TWO_COLOR)
    syms = slot_render_symbols(motif)
    assert [sid for sid, _ in syms] == [f"motif-{motif.id}-s0", f"motif-{motif.id}-s1"]
    s0_body, s1_body = syms[0][1], syms[1][1]
    # Active slot -> currentColor, the other -> none. No leftover slot tokens.
    assert 'fill="currentColor"' in s0_body and 'fill="none"' in s0_body
    assert 'fill="currentColor"' in s1_body and 'fill="none"' in s1_body
    for body in (s0_body, s1_body):
        assert 'fill="s0"' not in body and 'fill="s1"' not in body


def test_render_symbols_eleven_slots_no_substring_collision():
    # s1 vs s10/s11: the closing-quote anchor must prevent partial matches.
    inner = "".join(
        f'<rect x="{i}" y="0" width="1" height="1" fill="#{i:02d}00ff"/>'
        for i in range(11)
    )
    motif = normalize_motif_svg(_svg(inner))
    assert len(motif.color_slots) == 11
    for k in range(11):
        body = slot_render_symbols(motif)[k][1]
        assert body.count('fill="currentColor"') == 1
        assert body.count('fill="none"') == 10
        assert 'fill="s' not in body  # every token resolved, none left behind


def test_store_roundtrip_preserves_multicolor_slots():
    from app.motifs.store import MotifRecord

    motif = normalize_motif_svg(_TWO_COLOR)
    # Mirror persistence: color_slots arrives back as a jsonb list, not a tuple.
    rec = MotifRecord(
        id=motif.id,
        symbol=motif.symbol,
        bbox_mm=list(motif.bbox_mm),
        anchor=list(motif.anchor),
        variant_group="vg",
        color_slots=list(motif.color_slots),
    )
    restored = rec.to_motif_def()
    assert restored.color_slots == ("s0", "s1")
    assert [sid for sid, _ in slot_render_symbols(restored)] == [
        f"motif-{motif.id}-s0",
        f"motif-{motif.id}-s1",
    ]


# --- compose: multicolor binding ---------------------------------------------


def test_compose_binds_each_slot_to_its_palette_color():
    motif = normalize_motif_svg(_TWO_COLOR)
    register_motif(motif)
    slots = {"p0": "#00aa00", "p1": "#aa00aa"}
    raw = _intent(motif.id, {"colors": {"s0": "p0", "s1": "p1"}}, slots)
    result = validate_intent(raw)
    svg = compose(result.intent, result.palette, "default")
    root = ET.fromstring(svg)

    sym_ids = sorted(s.get("id") for s in root.findall(f".//{NS}symbol"))
    assert sym_ids == [f"motif-{motif.id}-s0", f"motif-{motif.id}-s1"]

    colors = {use.get("href"): use.get("color") for use in root.findall(f".//{NS}use")}
    assert colors[f"#motif-{motif.id}-s0"] == "#00aa00"
    assert colors[f"#motif-{motif.id}-s1"] == "#aa00aa"
    # No slot tokens leaked into the rendered document.
    assert 'fill="s0"' not in svg and 'fill="s1"' not in svg


def test_compose_three_slots():
    motif = normalize_motif_svg(_THREE_COLOR)
    register_motif(motif)
    slots = {"p0": "#112233", "p1": "#445566", "p2": "#778899"}
    raw = _intent(motif.id, {"colors": {"s0": "p0", "s1": "p1", "s2": "p2"}}, slots)
    result = validate_intent(raw)
    svg = compose(result.intent, result.palette, "default")
    root = ET.fromstring(svg)
    sym_ids = sorted(s.get("id") for s in root.findall(f".//{NS}symbol"))
    assert sym_ids == [f"motif-{motif.id}-s{k}" for k in range(3)]
    colors = {use.get("color") for use in root.findall(f".//{NS}use")}
    assert {"#112233", "#445566", "#778899"} <= colors


def test_compose_five_slots_end_to_end():
    # Pins the N=4..10 compose path (the pelican-multicolor example is N=5 but only a
    # script, so it cannot fail CI). N=11 elsewhere stops at slot_render_symbols.
    authoring = ("#ff0000", "#00ff00", "#0000ff", "#ffff00", "#ff00ff")
    inner = "".join(
        f'<rect x="{i * 20}" y="0" width="20" height="100" fill="{c}"/>'
        for i, c in enumerate(authoring)
    )
    motif = normalize_motif_svg(_svg(inner))
    register_motif(motif)
    assert motif.color_slots == ("s0", "s1", "s2", "s3", "s4")
    slots = {"p0": "#101010", "p1": "#202020", "p2": "#303030", "p3": "#404040",
             "p4": "#505050"}
    raw = _intent(motif.id, {"colors": {f"s{i}": f"p{i}" for i in range(5)}}, slots)
    result = validate_intent(raw)
    svg = compose(result.intent, result.palette, "default")
    root = ET.fromstring(svg)
    sym_ids = sorted(s.get("id") for s in root.findall(f".//{NS}symbol"))
    assert sym_ids == [f"motif-{motif.id}-s{k}" for k in range(5)]
    use_colors = {u.get("color") for u in root.findall(f".//{NS}use")}
    assert set(slots.values()) <= use_colors  # each slot bound to its palette color
    assert all(c not in svg for c in authoring)  # authoring colors tokenized away
    assert svg == compose(result.intent, result.palette, "default")  # deterministic


def test_symbol_is_colorway_agnostic_dedup_holds():
    motif = normalize_motif_svg(_TWO_COLOR)
    register_motif(motif)
    slots = {"p0": "#00aa00", "p1": "#aa00aa"}
    alt = {"p0": "#010203", "p1": "#040506"}
    raw = _intent(motif.id, {"colors": {"s0": "p0", "s1": "p1"}}, slots, extra_cw={"alt": alt})
    result = validate_intent(raw)
    a = compose(result.intent, result.palette, "default")
    b = compose(result.intent, result.palette, "alt")

    def _symbols(svg):
        root = ET.fromstring(svg)
        return {
            s.get("id"): ET.tostring(s, encoding="unicode")
            for s in root.findall(f".//{NS}symbol")
        }

    assert _symbols(a) == _symbols(b)  # symbol defs identical across colorways
    assert a != b  # but <use color> differs


def test_compose_multicolor_is_byte_deterministic():
    motif = normalize_motif_svg(_TWO_COLOR)
    register_motif(motif)
    slots = {"p0": "#00aa00", "p1": "#aa00aa"}
    raw = _intent(motif.id, {"colors": {"s0": "p0", "s1": "p1"}}, slots)
    result = validate_intent(raw)
    assert compose(result.intent, result.palette, "default") == compose(
        result.intent, result.palette, "default"
    )


# --- validation: colors <-> color_slots contract -----------------------------


def test_unbound_slot_is_rejected():
    motif = normalize_motif_svg(_TWO_COLOR)
    register_motif(motif)
    raw = _intent(motif.id, {"colors": {"s0": "p0"}}, {"p0": "#00aa00", "p1": "#aa00aa"})
    with pytest.raises(IntentInvalid):
        validate_intent(raw)


def test_extra_unknown_slot_key_is_rejected():
    motif = normalize_motif_svg(_TWO_COLOR)
    register_motif(motif)
    raw = _intent(
        motif.id,
        {"colors": {"s0": "p0", "s1": "p1", "s2": "p1"}},
        {"p0": "#00aa00", "p1": "#aa00aa"},
    )
    with pytest.raises(IntentInvalid):
        validate_intent(raw)


def test_single_color_param_on_multicolor_motif_is_rejected():
    motif = normalize_motif_svg(_TWO_COLOR)
    register_motif(motif)
    raw = _intent(motif.id, {"color": "p0"}, {"p0": "#00aa00", "p1": "#aa00aa"})
    with pytest.raises(IntentInvalid):
        validate_intent(raw)


def test_colors_value_must_be_known_palette_slot():
    motif = normalize_motif_svg(_TWO_COLOR)
    register_motif(motif)
    raw = _intent(
        motif.id, {"colors": {"s0": "p0", "s1": "nope"}}, {"p0": "#00aa00", "p1": "#aa00aa"}
    )
    with pytest.raises(IntentInvalid):
        validate_intent(raw)


def test_full_binding_is_valid():
    motif = normalize_motif_svg(_TWO_COLOR)
    register_motif(motif)
    raw = _intent(
        motif.id, {"colors": {"s0": "p0", "s1": "p1"}}, {"p0": "#00aa00", "p1": "#aa00aa"}
    )
    assert validate_intent(raw).warnings == []


# --- single-color backward compatibility --------------------------------------


def test_single_color_motif_composes_via_color():
    motif = normalize_motif_svg(_svg('<circle cx="50" cy="50" r="40" fill="#abc"/>'))
    register_motif(motif)
    raw = _intent(motif.id, {"color": "p0"}, {"p0": "#00aa00"})
    result = validate_intent(raw)
    svg = compose(result.intent, result.palette, "default")
    root = ET.fromstring(svg)
    sym_ids = [s.get("id") for s in root.findall(f".//{NS}symbol")]
    assert sym_ids == [f"motif-{motif.id}"]  # legacy single symbol, no -s suffix
    colors = {use.get("color") for use in root.findall(f".//{NS}use")}
    assert colors == {"#00aa00"}


# --- renderer pixel gate (method (b) end-to-end) ------------------------------


def _close(image: Image.Image, rgb: tuple[int, int, int], tol: int = 28) -> int:
    data = (
        list(image.get_flattened_data())
        if hasattr(image, "get_flattened_data")
        else list(image.getdata())
    )
    if data and isinstance(data[0], int):
        bands = len(image.getbands())
        data = [tuple(data[i : i + bands]) for i in range(0, len(data), bands)]
    count = 0
    for pixel in data:
        if all(abs(int(pixel[i]) - rgb[i]) <= tol for i in range(3)):
            count += 1
    return count


def test_multicolor_renders_each_slot_color_in_pixels():
    binary = find_renderer()
    if binary is None:
        pytest.skip("no SVG renderer (rsvg-convert/resvg); pixel gate skipped")
    motif = normalize_motif_svg(_TWO_COLOR)
    register_motif(motif)
    # Bind to colors that are NOT the authoring red/blue: proves tokenization swapped them.
    slots = {"bg": "#eeeeee", "p0": "#00cc00", "p1": "#cc00cc"}
    raw = _intent(motif.id, {"colors": {"s0": "p0", "s1": "p1"}}, slots)
    raw["layers"][1]["params"]["size_mm"] = 11.0
    result = validate_intent(raw)
    svg = compose(result.intent, result.palette, "default")
    png, _ = rasterize(svg, "png", 200, 48.0, binary=binary)
    image = Image.open(io.BytesIO(png)).convert("RGBA")

    assert _close(image, (0, 204, 0)) > 50  # slot s0 -> green present
    assert _close(image, (204, 0, 204)) > 50  # slot s1 -> magenta present
    # Authoring colors were tokenized away: no red/blue should survive.
    assert _close(image, (255, 0, 0)) == 0
    assert _close(image, (0, 0, 255)) == 0
