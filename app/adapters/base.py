"""Shared adapter types: the result envelope, the client-failure error, cache key.

Kept in a tiny leaf module so ``llm`` and ``image`` adapters can share these without
importing each other (avoids an import cycle).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field


@dataclass(frozen=True)
class AdapterResult:
    """What an adapter hands back to the route: a frozen intent + provenance.

    ``source_fidelity`` records the reproduction limit (``vector`` = clean intent;
    ``raster_hybrid`` = reference texture unfit for clean vectorization, see
    ``app.adapters.image``). ``warnings`` are non-fatal notes merged into the
    response by the route.
    """

    intent: dict
    source_fidelity: str = "vector"
    warnings: list[str] = field(default_factory=list)
    # Motif specs (subject/part/...) the LLM emitted alongside the intent, keyed to
    # motif layers by `layer_id`. The deterministic motif resolver (S10) consumes these
    # to inject a concrete motif_id; empty for the intent-direct / image paths.
    motif_specs: list[dict] = field(default_factory=list)


class AdapterClientError(RuntimeError):
    """An external adapter dependency (LLM / VLM / vectorizer) is unavailable or failed.

    Mapped to a 5xx at the API boundary — like a renderer failure, it is not the
    caller's fault. This is deliberately distinct from
    :class:`app.validate.intent.IntentInvalid` (a 422: the produced intent is
    semantically invalid even after the one allowed re-prompt).
    """


class ClientSlot:
    """Process-wide default-client slot: the set/get/resolve-or-raise trio every
    adapter needs, collapsed from four hand-rolled module-global copies.

    ``resolve`` prefers an explicitly injected client, falls back to the default, and
    raises ``error(message)`` when neither exists — or returns ``None`` if no ``error``
    was given (the embedding adapter's fail-soft contract)."""

    def __init__(self, error: type[AdapterClientError] | None = None, message: str = "") -> None:
        self._client = None
        self._error = error
        self._message = message

    def set(self, client) -> None:
        self._client = client

    def get(self):
        return self._client

    def resolve(self, client=None):
        if client is not None:
            return client
        if self._client is None and self._error is not None:
            raise self._error(self._message)
        return self._client


def cache_key(payload: dict) -> str:
    """Stable hash of an adapter's inputs, used to freeze/cache the produced intent.

    Same canonical-JSON serialization as ``determinism.layout_id_for`` so equal
    inputs always collide to the same key (key insertion order is irrelevant).
    """
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
