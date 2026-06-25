"""SVG sanitization: tag/attribute allowlist + href/color gates.

The engine emits SVG built from validated intent, with attribute values already
quote-escaped at the serialization layer (:mod:`app.render.svg`). This module is the
final gate that enforces what the output is *allowed* to contain, and the same gate
is reused for untrusted SVG entering the system: the ``export`` route's client SVG
and Recraft motif intake (:mod:`app.motifs.registry`).

Design: :func:`sanitize_svg` is a *validating gate*, not a rewriter. It parses with a
hardened XML parser (``defusedxml`` — no DTDs/external entities, so no XXE or
billion-laughs), walks the tree against the allowlist, and returns the input string
**unchanged** when it passes (so trusted engine output is byte-for-byte stable and the
structural regression tests keep passing). Any violation raises :class:`SanitizeError`.

Allowlist rationale:
- ``ALLOWED_TAGS`` covers exactly what the engine emits (svg/defs/symbol/pattern/g/
  rect/line/circle/ellipse/use) plus the vector primitives a Recraft motif may carry
  (path/polygon/polyline). ``<script>``/``<foreignObject>``/``<image>``/``<filter>``
  are *not* listed, so injection and embedded raster are rejected by construction.
- ``transform`` and ``patternTransform`` are allowed — they are load-bearing for
  per-instance ``<use>`` transforms (the spec risk note).
- ``href`` must be an internal ``#id`` fragment (no ``javascript:``/external URL).
- Color attributes (``fill``/``stroke``/``color``) accept ``currentColor``/``none``/a
  hex (``HEX_RE``) / internal ``url(#id)``; external ``url(...)`` and scheme-bearing
  tokens are rejected (paint-server SSRF). Other bare tokens (named/spot colors) pass.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET

from defusedxml import ElementTree as DefusedET
from defusedxml.common import DefusedXmlException

ALLOWED_TAGS: frozenset[str] = frozenset(
    {
        "svg",
        "defs",
        "symbol",
        "pattern",
        "g",
        "rect",
        "line",
        "circle",
        "ellipse",
        "use",
        "path",
        "polygon",
        "polyline",
    }
)

ALLOWED_ATTRS: frozenset[str] = frozenset(
    {
        "xmlns",
        "width",
        "height",
        "viewBox",
        "id",
        "overflow",
        "patternUnits",
        "patternTransform",
        "x",
        "y",
        "cx",
        "cy",
        "r",
        "rx",
        "ry",
        "x1",
        "y1",
        "x2",
        "y2",
        "points",
        "d",
        "fill",
        "stroke",
        "stroke-width",
        "color",
        "opacity",
        "transform",
        "href",
    }
)

COLOR_ATTRS: frozenset[str] = frozenset({"fill", "stroke", "color"})

# Hex gate from the spec: #RGB / #RGBA / #RRGGBB / #RRGGBBAA.
HEX_RE = re.compile(r"^#[0-9a-fA-F]{3,8}$")
# Internal paint-server reference only, e.g. url(#tile).
_URL_INTERNAL_RE = re.compile(r"^url\(#[A-Za-z0-9_\-:.]+\)$")
# A strict internal fragment: '#' + an id token (no embedded whitespace/extra tokens).
_FRAGMENT_RE = re.compile(r"^#[A-Za-z_][\w.\-:]*$")


class SanitizeError(ValueError):
    """Raised when an SVG contains a tag/attribute/value outside the allowlist."""


def _local(name: str) -> str:
    """Strip an ``{namespace}`` prefix from a tag or attribute name."""
    return name.split("}", 1)[1] if "}" in name else name


def is_internal_href(value: str) -> bool:
    """True only for a strict internal ``#id`` fragment (the sole allowed href form).

    Requires a single fragment token: ``#tile`` passes, but ``#x javascript:y`` (extra
    tokens after the id) and external/scheme URLs do not.
    """
    return bool(_FRAGMENT_RE.match(value.strip()))


def _check_color(attr: str, value: str) -> None:
    s = value.strip()
    if not s:
        return
    low = s.lower()
    # Valid CSS-wide / paint keywords the engine and authored motifs may use.
    if low in ("currentcolor", "none", "transparent", "inherit"):
        return
    if s.startswith("#"):
        if not HEX_RE.match(s):
            raise SanitizeError(f"invalid hex in {attr}={value!r}")
        return
    if low.startswith("url("):
        if not _URL_INTERNAL_RE.match(s):
            raise SanitizeError(f"non-internal paint reference in {attr}={value!r}")
        return
    # Reject scheme-bearing tokens (javascript:, data:, http:) outside url(...).
    head = s.split("(", 1)[0]
    if ":" in head:
        raise SanitizeError(f"scheme not allowed in {attr}={value!r}")
    # Bare token: named color / Pantone-TCX spot code / rgb(...) — harmless, allowed.


def validate_tree(root: ET.Element) -> None:
    """Walk a (namespace-stripped) element tree against the allowlist.

    Raises :class:`SanitizeError` on the first disallowed tag, attribute, external
    href, or unsafe color value.
    """
    for el in root.iter():
        if not isinstance(el.tag, str):
            # Comments / processing instructions have non-str tags; reject defensively.
            raise SanitizeError("non-element node not allowed")
        tag = _local(el.tag)
        if tag not in ALLOWED_TAGS:
            raise SanitizeError(f"tag <{tag}> not allowed")
        for raw_key, value in el.attrib.items():
            key = _local(raw_key)
            if key not in ALLOWED_ATTRS:
                raise SanitizeError(f"attribute {key!r} on <{tag}> not allowed")
            if key == "href":
                if not is_internal_href(value):
                    raise SanitizeError(f"non-internal href {value!r} not allowed")
            elif key in COLOR_ATTRS:
                _check_color(key, value)


def parse_svg(svg: str) -> ET.Element:
    """Parse untrusted SVG with a hardened parser and strip XML namespaces.

    Returns the root element with all tags/attributes reduced to local names (so
    downstream serialization is namespace-clean). Raises :class:`SanitizeError` for
    malformed XML, forbidden DTDs/entities (XXE/billion-laughs), or trailing content
    (e.g. a ``</svg><script>`` break-out attempt).
    """
    try:
        root = DefusedET.fromstring(svg)
    except (ET.ParseError, DefusedXmlException, ValueError) as exc:
        raise SanitizeError(f"unparseable or unsafe SVG: {exc}") from None
    for el in root.iter():
        if not isinstance(el.tag, str):
            continue
        el.tag = _local(el.tag)
        if any("}" in k for k in el.attrib):
            # Collapsing namespaces must be fail-closed: if a namespaced attribute and a
            # plain one share a local name (e.g. xlink:href + href), a naive dict would
            # keep only the last and a malicious value could hide behind a safe sibling
            # while still reaching the renderer. Reject the ambiguity instead.
            collapsed: dict[str, str] = {}
            for key, value in el.attrib.items():
                local = _local(key)
                if local in collapsed:
                    raise SanitizeError(
                        f"ambiguous namespaced attribute {local!r} on <{el.tag}>"
                    )
                collapsed[local] = value
            el.attrib = collapsed
    return root


def sanitize_svg(svg: str) -> str:
    """Validating gate for a complete SVG document (byte-stable).

    Returns ``svg`` unchanged when it satisfies the allowlist; raises
    :class:`SanitizeError` otherwise. Used on the generate/composition output path,
    where the input is trusted engine output and byte-stability matters (determinism).
    For untrusted input that should be scrubbed, use :func:`scrub_svg`.
    """
    root = parse_svg(svg)
    validate_tree(root)
    return svg


def scrub_svg(svg: str) -> str:
    """Validate untrusted SVG and return a re-serialized, scrubbed copy.

    Unlike :func:`sanitize_svg` (a byte-stable gate for trusted engine output), this
    re-serializes the validated element tree, so anything the parser dropped from the
    tree — XML comments, processing instructions, CDATA, foreign nodes — cannot survive
    into the output. The rendered bytes then equal exactly what was validated. Used on
    the untrusted export path; raises :class:`SanitizeError` on any violation.
    """
    root = parse_svg(svg)
    validate_tree(root)
    # parse_svg consumes the default namespace; re-add it so the output is valid SVG.
    root.set("xmlns", "http://www.w3.org/2000/svg")
    return ET.tostring(root, encoding="unicode")
