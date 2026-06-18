"""Session-8 sanitize gate: SVG injection is blocked, legitimate engine output passes
through unchanged, and the export route rejects unsafe client SVG."""

import pytest
from fastapi.testclient import TestClient

from app.engine.generate import generate
from app.main import app
from app.render.sanitize import HEX_RE, SanitizeError, sanitize_svg
from tests.test_intent import mvp_intent

client = TestClient(app)

_NS = '<svg xmlns="http://www.w3.org/2000/svg"'


@pytest.mark.parametrize(
    "payload",
    [
        f"{_NS}><rect/></svg><script>alert(1)</script>",  # </svg> break-out + trailing
        f"{_NS}><script>alert(1)</script></svg>",  # script tag inside
        f'{_NS}><use href="javascript:alert(1)"/></svg>',  # javascript href
        f'{_NS}><image href="http://evil/x.png"/></svg>',  # external embedded raster
        f"{_NS}><foreignObject/></svg>",  # disallowed tag
        f'{_NS} onload="x()"><rect/></svg>',  # disallowed attribute
        f'{_NS}><rect fill="url(http://evil/p)"/></svg>',  # external paint server (SSRF)
        f'{_NS}><filter id="f"/></svg>',  # filter not in the allowlist
    ],
)
def test_sanitize_blocks_injection(payload):
    with pytest.raises(SanitizeError):
        sanitize_svg(payload)


def test_sanitize_blocks_dtd_entity_definition():
    # defusedxml refuses DTD entity definitions (XXE / billion-laughs vector).
    xxe = (
        '<!DOCTYPE svg [<!ENTITY a "AAA">]>'
        f"{_NS}><rect/></svg>"
    )
    with pytest.raises(SanitizeError):
        sanitize_svg(xxe)


def test_sanitize_passes_engine_output_unchanged():
    svg = generate(mvp_intent()).svg
    assert sanitize_svg(svg) == svg  # validating gate, not a rewriter
    assert "<pattern" in svg  # enumerate-guard: tile is a <pattern>


def test_sanitize_preserves_pattern_transform_and_use_transform():
    # The spec risk: the allowlist must not block legitimate transforms.
    svg = (
        f"{_NS}>"
        '<defs><symbol id="motif-x" overflow="visible">'
        '<circle cx="0" cy="0" r="0.5" fill="currentColor"/></symbol>'
        '<pattern id="tile" patternUnits="userSpaceOnUse" '
        'patternTransform="rotate(10)" width="10" height="10">'
        '<use href="#motif-x" color="#abc" transform="translate(1 2) scale(3)"/>'
        "</pattern></defs>"
        '<rect fill="url(#tile)"/></svg>'
    )
    assert sanitize_svg(svg) == svg


def test_sanitize_allows_spot_color_token():
    # resolve_color may emit a Pantone/TCX spot string; it is not an injection.
    svg = f'{_NS}><rect fill="19-4024 TCX"/></svg>'
    assert sanitize_svg(svg) == svg


@pytest.mark.parametrize(
    "value,ok",
    [
        ("#abc", True),
        ("#abcd", True),
        ("#aabbcc", True),
        ("#aabbccdd", True),
        ("#xyz", False),
        ("#12", False),
        ("aabbcc", False),
    ],
)
def test_hex_regex(value, ok):
    assert bool(HEX_RE.match(value)) is ok


def test_export_route_rejects_unsafe_svg_400():
    bad = f'{_NS}><image href="http://evil/x.png"/></svg>'
    resp = client.post(
        "/api/v1/export", json={"svg": bad, "dpi": 300, "width_mm": 48}
    )
    assert resp.status_code == 400
    assert "unsafe svg" in str(resp.json()["detail"]).lower()


def test_export_route_rejects_script_breakout_400():
    bad = f"{_NS}><rect/></svg><script>alert(1)</script>"
    resp = client.post(
        "/api/v1/export", json={"svg": bad, "dpi": 300, "width_mm": 48}
    )
    assert resp.status_code == 400


@pytest.mark.parametrize(
    "payload",
    [
        # xlink:href masks a dangerous value behind a safe plain href (both orders).
        f'{_NS} xmlns:xlink="http://www.w3.org/1999/xlink">'
        '<use xlink:href="javascript:evil" href="#safe"/></svg>',
        f'{_NS} xmlns:xlink="http://www.w3.org/1999/xlink">'
        '<use href="#safe" xlink:href="javascript:evil"/></svg>',
        # custom-ns paint masks a safe fill -> external paint-server would survive.
        f'{_NS} xmlns:z="urn:z"><rect z:fill="url(http://evil)" fill="#fff"/></svg>',
    ],
)
def test_sanitize_blocks_namespaced_attribute_collision(payload):
    with pytest.raises(SanitizeError):
        sanitize_svg(payload)


def test_sanitize_allows_lone_internal_xlink_href():
    # A single namespaced internal reference is legitimate and must pass unchanged.
    svg = f'{_NS} xmlns:xlink="http://www.w3.org/1999/xlink"><use xlink:href="#tile"/></svg>'
    assert sanitize_svg(svg) == svg


def test_sanitize_blocks_lone_external_xlink_href():
    # No masking sibling: the namespace collapses to href and the allowlist gate rejects it.
    svg = f'{_NS} xmlns:xlink="http://www.w3.org/1999/xlink"><use xlink:href="http://evil"/></svg>'
    with pytest.raises(SanitizeError):
        sanitize_svg(svg)


def test_scrub_svg_drops_comments_and_pi():
    from app.render.sanitize import scrub_svg

    svg = f'{_NS}><!--<script>x</script>--><?php evil ?><rect width="1" height="1" fill="#abc"/></svg>'
    out = scrub_svg(svg)
    assert "<!--" not in out and "<?php" not in out  # non-element nodes scrubbed
    assert "<rect" in out and 'xmlns="http://www.w3.org/2000/svg"' in out
    assert sanitize_svg(out) == out  # the scrubbed output itself revalidates clean


def test_scrub_svg_blocks_injection_and_collision():
    from app.render.sanitize import scrub_svg

    with pytest.raises(SanitizeError):
        scrub_svg(f"{_NS}><script>x</script></svg>")
    with pytest.raises(SanitizeError):
        scrub_svg(
            f'{_NS} xmlns:xlink="http://www.w3.org/1999/xlink">'
            '<use xlink:href="javascript:e" href="#s"/></svg>'
        )


def test_sanitize_allows_rgb_and_internal_url_color_tokens():
    # A legitimate rgb() color and an internal url(#id) paint reference must pass.
    svg = f'{_NS}><rect fill="rgb(1,2,3)" stroke="url(#tile)"/></svg>'
    assert sanitize_svg(svg) == svg
