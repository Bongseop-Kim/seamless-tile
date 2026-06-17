import xml.etree.ElementTree as ET
from collections import Counter

from app.engine.composition import _instance_transform, compose
from app.engine.placement import Instance
from app.motifs.registry import MotifDef
from app.validate.intent import validate_intent
from tests.test_intent import mvp_intent

NS = "{http://www.w3.org/2000/svg}"


def _compose_mvp(colorway_id: str | None = None):
    result = validate_intent(mvp_intent())
    return result, compose(result.intent, result.palette, colorway_id)


def _hrefs(root) -> Counter:
    return Counter(use.get("href") for use in root.findall(f".//{NS}use"))


def test_exactly_one_pattern_in_user_space():
    _, svg = _compose_mvp()
    root = ET.fromstring(svg)
    patterns = root.findall(f".//{NS}pattern")
    assert len(patterns) == 1
    pattern = patterns[0]
    assert pattern.get("patternUnits") == "userSpaceOnUse"
    assert pattern.get("width") == "48"
    assert pattern.get("height") == "48"


def test_document_uses_the_pattern():
    _, svg = _compose_mvp()
    root = ET.fromstring(svg)
    fills = [rect.get("fill") for rect in root.findall(f"{NS}rect")]
    assert "url(#tile)" in fills


def test_one_symbol_per_distinct_motif():
    _, svg = _compose_mvp()
    root = ET.fromstring(svg)
    ids = sorted(s.get("id") for s in root.findall(f".//{NS}symbol"))
    assert ids == ["motif-bee", "motif-circle"]


def test_multiple_uses_per_motif():
    _, svg = _compose_mvp()
    counts = _hrefs(ET.fromstring(svg))
    assert counts["#motif-circle"] > 1
    assert counts["#motif-bee"] > 1


def test_no_instance_enumeration_regression_guard():
    # Geometry is defined ONCE per <symbol>; instances are <use>. Raw geometry
    # element counts must equal the per-symbol counts, never the instance count.
    _, svg = _compose_mvp()
    root = ET.fromstring(svg)
    circles = root.findall(f".//{NS}circle")
    ellipses = root.findall(f".//{NS}ellipse")
    uses = root.findall(f".//{NS}use")
    assert len(circles) == 1  # circle symbol geometry only
    assert len(ellipses) == 3  # bee symbol geometry only
    assert len(uses) > len(circles) + len(ellipses)


def test_use_color_binds_resolved_colorway():
    result, svg = _compose_mvp()
    root = ET.fromstring(svg)
    colors = {use.get("href"): use.get("color") for use in root.findall(f".//{NS}use")}
    assert colors["#motif-circle"] == result.palette.resolve_color("accent", None)
    assert colors["#motif-bee"] == result.palette.resolve_color("gold", None)


def test_colorway_switch_changes_output_and_color():
    raw = mvp_intent()
    raw["colorways"].append(
        {
            "id": "alt",
            "name": "alt",
            "mapping": {"ground": "#000000", "accent": "#112233", "gold": "#445566"},
        }
    )
    result = validate_intent(raw)
    default_svg = compose(result.intent, result.palette, "default")
    alt_svg = compose(result.intent, result.palette, "alt")
    assert default_svg != alt_svg
    colors = {
        use.get("href"): use.get("color")
        for use in ET.fromstring(alt_svg).findall(f".//{NS}use")
    }
    assert colors["#motif-circle"] == "#112233"
    assert colors["#motif-bee"] == "#445566"


def test_layers_composed_in_z_order():
    _, svg = _compose_mvp()
    # background (z0) < stripe (z1) < motifs (z2, z3): assert the order of the
    # pattern's direct children by tag, parsed (not raw-string matched).
    pattern = ET.fromstring(svg).find(f".//{NS}pattern")
    child_tags = [child.tag.removeprefix(NS) for child in pattern]
    assert child_tags[0] == "rect"  # background fill
    assert "g" in child_tags  # stripe group
    assert child_tags.index("g") < child_tags.index("use")  # stripe before motifs


def test_compose_is_byte_deterministic():
    result = validate_intent(mvp_intent())
    assert compose(result.intent, result.palette) == compose(result.intent, result.palette)


def test_unit_box_motif_scales_by_size_mm():
    # extent 1.0, anchor at origin -> scale == size_mm, no anchor translate.
    motif = MotifDef(id="x", symbol="", bbox_mm=(-0.5, -0.5, 0.5, 0.5), anchor=(0.0, 0.0))
    transform = _instance_transform(motif, Instance(10.0, 20.0, 0.0), size_mm=4.0)
    assert transform == "translate(10 20) rotate(0) scale(4)"


def test_instance_transform_honors_bbox_extent_and_anchor():
    # Non-unit bbox (extent 2.0) and an off-origin anchor must both be load-bearing:
    # scale = size_mm / extent, and the anchor is shifted to the origin (rightmost).
    motif = MotifDef(id="x", symbol="", bbox_mm=(0.0, 0.0, 2.0, 1.0), anchor=(1.0, 0.5))
    transform = _instance_transform(motif, Instance(10.0, 20.0, 30.0), size_mm=4.0)
    assert transform == "translate(10 20) rotate(30) scale(2) translate(-1 -0.5)"
