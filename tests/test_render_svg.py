import xml.etree.ElementTree as ET

from app.render.svg import render_svg_document

SVG_NS = "{http://www.w3.org/2000/svg}"


def test_render_svg_document_wraps_composed_body():
    svg = render_svg_document('<rect width="10" height="20"/>', 10, 20)
    root = ET.fromstring(svg)
    assert root.tag == f"{SVG_NS}svg"
    assert root.get("width") == "10mm"
    assert root.get("height") == "20mm"
    assert root.find(f"{SVG_NS}rect") is not None
