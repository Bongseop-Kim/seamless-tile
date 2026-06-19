"""Session-11 embedding adapter: freeze cache, graceful-when-unconfigured, errors.

No network and no SDK: a fake client implements the EmbeddingClient seam. Mirrors the
llm/recraft adapter test style (injected protocol, process-global reset around tests).
"""

from types import SimpleNamespace

import pytest

from app.adapters.embedding import (
    EMBEDDING_CACHE_MAX_SIZE,
    EmbeddingError,
    clear_embedding_cache,
    client_from_settings,
    embed_query,
    set_default_embedding_client,
)


@pytest.fixture(autouse=True)
def _clean():
    def _purge():
        clear_embedding_cache()
        set_default_embedding_client(None)

    _purge()
    yield
    _purge()


class _FakeEmbed:
    def __init__(self, vector=(0.1, 0.2, 0.3), model="fake-model") -> None:
        self.model = model
        self.vector = list(vector)
        self.calls = 0

    def embed(self, text: str) -> list[float]:
        self.calls += 1
        return list(self.vector)


def test_embed_query_none_when_unconfigured():
    # No injected client and no default -> graceful None (soft-similarity unavailable).
    assert embed_query("anything") is None


def test_embed_query_uses_injected_client():
    fake = _FakeEmbed()
    assert embed_query("pig", client=fake) == [0.1, 0.2, 0.3]
    assert fake.calls == 1


def test_embed_query_freeze_cache():
    fake = _FakeEmbed()
    embed_query("pig", client=fake)
    embed_query("pig", client=fake)  # same text -> cache hit (no second call)
    assert fake.calls == 1
    embed_query("cow", client=fake)  # different text -> new call
    assert fake.calls == 2


def test_embed_query_cache_keyed_by_model():
    a = _FakeEmbed(vector=(1.0,), model="m1")
    b = _FakeEmbed(vector=(2.0,), model="m2")
    assert embed_query("x", client=a) == [1.0]
    assert embed_query("x", client=b) == [2.0]  # different model id => no cache collision


def test_embed_query_uses_default_client():
    set_default_embedding_client(_FakeEmbed(vector=(9.0,)))
    assert embed_query("x") == [9.0]


def test_embed_query_propagates_upstream_error():
    class _Boom:
        model = "m"

        def embed(self, text):
            raise EmbeddingError("down")

    with pytest.raises(EmbeddingError):
        embed_query("x", client=_Boom(), use_cache=False)


def test_embed_query_normalizes_unexpected_error():
    class _Boom:
        model = "m"

        def embed(self, text):
            raise RuntimeError("down")

    with pytest.raises(EmbeddingError, match="down"):
        embed_query("x", client=_Boom(), use_cache=False)


def test_embed_query_cache_is_bounded_lru():
    fake = _FakeEmbed()
    for i in range(EMBEDDING_CACHE_MAX_SIZE + 1):
        embed_query(f"text-{i}", client=fake)
    assert fake.calls == EMBEDDING_CACHE_MAX_SIZE + 1

    embed_query("text-0", client=fake)
    assert fake.calls == EMBEDDING_CACHE_MAX_SIZE + 2


def test_client_from_settings_none_without_key():
    assert client_from_settings(SimpleNamespace(openai_api_key=None)) is None
    assert client_from_settings(SimpleNamespace(openai_api_key="")) is None
