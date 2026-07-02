"""Embedding adapter (D12): ``text -> vector`` for descriptor soft-similarity.

The embedding model is a SEPARATE model from the chat LLM (D12): OpenAI
``text-embedding-3-small``. Like the other adapters, the client is an injected seam
(a ``Protocol``); the concrete client is a direct ``httpx`` POST to
``/v1/embeddings`` (no SDK). Real network calls happen only when
:func:`embed_query` is invoked.

This adapter lives OUTSIDE the engine's determinism boundary. The motif resolver uses
the returned vector only to decide *which* concrete ``motif_id`` to reuse/generate; the
chosen id is then frozen into the resolved-intent snapshot (the actual reproduction
unit, spec §7.3/D17). The in-process cache here is a cost optimization, not the
determinism guarantee.

The app requires ``OPENAI_API_KEY`` at startup. For direct unit tests or explicitly
injected resolver calls, no client still means :func:`embed_query` returns ``None`` and
the resolver skips the soft-similarity stage. Network failures are normalized to
:class:`~app.adapters.base.AdapterClientError`.
"""

from __future__ import annotations

from collections import OrderedDict
from typing import Protocol

import httpx

from app.adapters.base import AdapterClientError, ClientSlot, cache_key

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
    """``embed(text) -> list[float]`` via a direct POST to OpenAI ``/v1/embeddings``.

    ponytail: one endpoint, one call — httpx (already a dependency) instead of the
    openai SDK."""

    def __init__(self, api_key: str, model: str = DEFAULT_MODEL) -> None:
        if not api_key:
            raise EmbeddingError("OpenAIEmbeddingClient requires a non-empty api_key")
        self.model = model
        self._api_key = api_key

    def embed(self, text: str) -> list[float]:
        try:
            resp = httpx.post(
                "https://api.openai.com/v1/embeddings",
                headers={"Authorization": f"Bearer {self._api_key}"},
                json={"model": self.model, "input": text},
                timeout=30.0,
            )
            resp.raise_for_status()
        except Exception as exc:  # transport / HTTP / API failure
            raise EmbeddingError(f"OpenAI embedding request failed: {exc}") from exc
        try:
            return list(resp.json()["data"][0]["embedding"])
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise EmbeddingError(f"OpenAI returned an unexpected payload: {exc}") from exc


# No error type: resolve() returning None means "soft-similarity unavailable".
_slot = ClientSlot()
set_default_embedding_client = _slot.set
get_default_embedding_client = _slot.get


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
    resolved = _slot.resolve(client)
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
    """Build an :class:`OpenAIEmbeddingClient` from settings.

    ``app.main`` treats a missing key as startup misconfiguration before calling this;
    returning ``None`` keeps low-level tests and direct adapter use simple.
    """
    api_key = getattr(settings, "openai_api_key", None)
    if not api_key:
        return None
    model = getattr(settings, "embedding_model", None) or DEFAULT_MODEL
    return OpenAIEmbeddingClient(api_key, model)
