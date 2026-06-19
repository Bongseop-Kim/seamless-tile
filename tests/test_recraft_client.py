"""Session-13 RecraftHTTPClient: offline HTTP-contract tests (httpx.MockTransport).

No real network: a mock transport asserts the request shape (vector endpoint, bearer
auth, vector model/style, response_format) and returns canned payloads, so the wiring is
verified before any real Recraft credits are spent. The real call lives in
scripts/recraft_smoke.py (manual, key-gated).
"""

import base64
import json
from types import SimpleNamespace

import httpx
import pytest

from app.adapters.recraft import (
    RecraftError,
    RecraftHTTPClient,
    client_from_settings,
    clear_recraft_motif_cache,
    generate_via_recraft,
)
from app.motifs.registry import MOTIFS, get_motif

_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10">'
    '<path d="M0 0H10V10H0Z" fill="#aabbcc"/></svg>'
)


@pytest.fixture(autouse=True)
def _clean():
    def _purge():
        clear_recraft_motif_cache()
        for key in [k for k in MOTIFS if k.startswith("recraft-")]:
            del MOTIFS[key]

    _purge()
    yield
    _purge()


def test_url_format_posts_generations_then_fetches_file():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            seen["path"] = request.url.path
            seen["auth"] = request.headers.get("authorization")
            seen["body"] = json.loads(request.content)
            return httpx.Response(
                200, json={"data": [{"url": "https://files.recraft.test/x.svg"}]}
            )
        return httpx.Response(200, text=_SVG)  # the follow-up GET for the .svg file

    client = RecraftHTTPClient("tok", transport=httpx.MockTransport(handler))
    out = client.generate("a red dot")

    assert out == _SVG
    assert seen["path"].endswith("/images/generations")  # standard endpoint, not /vector
    assert seen["auth"] == "Bearer tok"
    assert seen["body"]["prompt"] == "a red dot"
    assert seen["body"]["model"].endswith("_vector")
    assert "style" not in seen["body"]  # omitted by default; the vector model drives SVG
    assert seen["body"]["response_format"] == "url"


def test_style_included_only_when_configured():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            seen["body"] = json.loads(request.content)
            return httpx.Response(200, json={"data": [{"url": "https://f.test/x.svg"}]})
        return httpx.Response(200, text=_SVG)

    client = RecraftHTTPClient(
        "tok", style="vector_illustration", transport=httpx.MockTransport(handler)
    )
    client.generate("x")
    assert seen["body"]["style"] == "vector_illustration"


def test_b64_json_format_decodes_inline_svg():
    b64 = base64.b64encode(_SVG.encode()).decode()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"b64_json": b64}]})

    client = RecraftHTTPClient(
        "tok", response_format="b64_json", transport=httpx.MockTransport(handler)
    )
    assert client.generate("x") == _SVG


def test_http_error_maps_to_recraft_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="invalid token")

    client = RecraftHTTPClient("tok", transport=httpx.MockTransport(handler))
    with pytest.raises(RecraftError):
        client.generate("x")


def test_non_svg_payload_raises():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(200, json={"data": [{"url": "https://f.test/x"}]})
        return httpx.Response(200, text="not an svg at all")

    client = RecraftHTTPClient("tok", transport=httpx.MockTransport(handler))
    with pytest.raises(RecraftError):
        client.generate("x")


def test_empty_api_key_rejected():
    with pytest.raises(RecraftError):
        RecraftHTTPClient("")


def test_client_from_settings_none_without_key():
    assert client_from_settings(SimpleNamespace(recraft_api_key=None)) is None
    assert client_from_settings(SimpleNamespace(recraft_api_key="")) is None


def test_client_from_settings_builds_with_key():
    settings = SimpleNamespace(
        recraft_api_key="tok",
        recraft_model="recraftv4_vector",
        recraft_style="vector_illustration",
        recraft_size="1024x1024",
        recraft_response_format="url",
        recraft_base_url="https://external.api.recraft.ai/v1",
    )
    client = client_from_settings(settings)
    assert isinstance(client, RecraftHTTPClient)


def test_end_to_end_http_client_through_gate_and_register():
    # The real-shaped path: Recraft returns a gradient SVG, the gate flattens it, and the
    # motif registers — proving RecraftHTTPClient + _flatten_unsuitable + register_motif
    # compose correctly (all offline via MockTransport).
    grad = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><defs>'
        '<linearGradient id="g"><stop offset="0" stop-color="#00aa00"/></linearGradient>'
        '</defs><rect x="0" y="0" width="10" height="10" fill="url(#g)"/></svg>'
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(200, json={"data": [{"url": "https://f.test/x.svg"}]})
        return httpx.Response(200, text=grad)

    client = RecraftHTTPClient("tok", transport=httpx.MockTransport(handler))
    mid = generate_via_recraft(
        {"layer_id": "m", "subject": "pig", "part": "face"},
        client=client,
        use_cache=False,
    )
    assert get_motif(mid).id == mid
