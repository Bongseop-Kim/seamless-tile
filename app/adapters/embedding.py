"""Embedding adapter (D12): ``text -> vector`` for descriptor soft-similarity.

The embedding model is a SEPARATE model from the chat LLM (D12): OpenAI
``text-embedding-3-small``. Like the other adapters, the client is an injected seam
(a ``Protocol``) and the SDK is imported lazily — no ``openai`` dependency is forced
on import or on the test suite. Real network calls are opt-in: :func:`app.main.lifespan`
installs a default client only when ``OPENAI_API_KEY`` is configured.

This adapter lives OUTSIDE the engine's determinism boundary. The motif resolver uses
the returned vector only to decide *which* concrete ``motif_id`` to reuse/generate; the
chosen id is then frozen into the resolved-intent snapshot (the actual reproduction
unit, spec §7.3/D17). The in-process cache here is a cost optimization, not the
determinism guarantee.

When no client is configured, :func:`embed_query` returns ``None`` (the resolver then
skips the soft-similarity stage and falls back to S10 behavior). SDK/network failures
are normalized to :class:`~app.adapters.base.AdapterClientError`.
"""

from __future__ import annotations

from collections import OrderedDict
from typing import Protocol

from app.adapters.base import AdapterClientError, cache_key

DEFAULT_MODEL = "text-embedding-3-small"
EMBEDDING_CACHE_MAX_SIZE = 512


class EmbeddingClient(Protocol):
    """Minimal embedding seam. ``model`` lets :func:`embed_query` key its cache by
    model id, so swapping models cannot collide with previously cached vectors."""

    model: str

    def embed(self, text: str) -> list[float]: ...


class EmbeddingError(AdapterClientError):
    """Embedding upstream failed (502-class). The resolver treats this fail-soft."""


class OpenAIEmbeddingClient:
    """Adapts the ``openai`` SDK to the ``embed(text) -> list[float]`` seam."""

    def __init__(self, api_key: str, model: str = DEFAULT_MODEL) -> None:
        if not api_key:
            raise EmbeddingError("OpenAIEmbeddingClient requires a non-empty api_key")
        self.model = model
        try:
            from openai import OpenAI
        except ImportError as exc:  # dependency not installed
            raise EmbeddingError(f"openai is not installed: {exc}") from exc
        self._client = OpenAI(api_key=api_key)

    def embed(self, text: str) -> list[float]:
        try:
            response = self._client.embeddings.create(model=self.model, input=text)
        except Exception as exc:  # transport / SDK / API failure
            raise EmbeddingError(f"OpenAI embedding request failed: {exc}") from exc
        try:
            return list(response.data[0].embedding)
        except (AttributeError, IndexError, TypeError) as exc:
            raise EmbeddingError(f"OpenAI returned an unexpected payload: {exc}") from exc


_DEFAULT_EMBEDDING_CLIENT: EmbeddingClient | None = None


def set_default_embedding_client(client: EmbeddingClient | None) -> None:
    """Register a process-wide default embedding client (opt-in; used for real calls)."""
    global _DEFAULT_EMBEDDING_CLIENT
    _DEFAULT_EMBEDDING_CLIENT = client


def get_default_embedding_client() -> EmbeddingClient | None:
    return _DEFAULT_EMBEDDING_CLIENT


# Process-local freeze cache: same (model, text) -> same vector within the process.
_embedding_cache: OrderedDict[str, list[float]] = OrderedDict()


def clear_embedding_cache() -> None:
    _embedding_cache.clear()


def embed_query(
    text: str,
    *,
    client: EmbeddingClient | None = None,
    use_cache: bool = True,
) -> list[float] | None:
    """Embed ``text``, or return ``None`` when no embedding client is available.

    Resolution order: the injected ``client`` else the process default. A ``None``
    result means "soft-similarity unavailable" (graceful, not an error). Upstream
    failures raise :class:`EmbeddingError` for the caller to handle (the resolver
    catches it and falls back, spec §6.4 graceful-degradation precedent).
    """
    resolved = client if client is not None else _DEFAULT_EMBEDDING_CLIENT
    if resolved is None:
        return None
    model = getattr(resolved, "model", DEFAULT_MODEL)
    key = cache_key({"k": "embedding", "model": model, "text": text})
    if use_cache and key in _embedding_cache:
        _embedding_cache.move_to_end(key)
        return _embedding_cache[key]
    try:
        vector = resolved.embed(text)
    except EmbeddingError:
        raise
    except Exception as exc:
        raise EmbeddingError(f"embedding request failed for model {model!r}: {exc}") from exc
    if use_cache:
        _embedding_cache[key] = vector
        _embedding_cache.move_to_end(key)
        if len(_embedding_cache) > EMBEDDING_CACHE_MAX_SIZE:
            _embedding_cache.popitem(last=False)
    return vector


def client_from_settings(settings) -> OpenAIEmbeddingClient | None:
    """Build an :class:`OpenAIEmbeddingClient` iff ``openai_api_key`` is set, else ``None``."""
    api_key = getattr(settings, "openai_api_key", None)
    if not api_key:
        return None
    model = getattr(settings, "embedding_model", None) or DEFAULT_MODEL
    return OpenAIEmbeddingClient(api_key, model)
