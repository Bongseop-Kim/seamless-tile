import xml.etree.ElementTree as ET

from app.render.svg import escape_attr, escape_text, render_svg_document

SVG_NS = "{http://www.w3.org/2000/svg}"


def test_render_svg_document_wraps_composed_body():
    svg = render_svg_document('<rect width="10" height="20"/>', 10, 20)
    root = ET.fromstring(svg)
    assert root.tag == f"{SVG_NS}svg"
    assert root.get("width") == "10mm"
    assert root.get("height") == "20mm"
    assert root.find(f"{SVG_NS}rect") is not None


def test_escape_attr_escapes_quotes_and_brackets():
    assert escape_attr('a"<&>\'') == "a&quot;&lt;&amp;&gt;&#39;"


def test_escape_text_escapes_brackets_and_amp():
    assert escape_text("<tag> & </tag>") == "&lt;tag&gt; &amp; &lt;/tag&gt;"


def test_escaped_attr_round_trips_through_parser():
    frag = f'<rect fill="{escape_attr(chr(34) + "a&b<c>" + chr(34))}"/>'
    el = ET.fromstring(frag)
    assert el.get("fill") == '"a&b<c>"'
