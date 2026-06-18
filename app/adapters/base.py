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


class AdapterClientError(RuntimeError):
    """An external adapter dependency (LLM / VLM / vectorizer) is unavailable or failed.

    Mapped to a 5xx at the API boundary — like a renderer failure, it is not the
    caller's fault. This is deliberately distinct from
    :class:`app.validate.intent.IntentInvalid` (a 422: the produced intent is
    semantically invalid even after the one allowed re-prompt).
    """


def cache_key(payload: dict) -> str:
    """Stable hash of an adapter's inputs, used to freeze/cache the produced intent.

    Same canonical-JSON serialization as ``determinism.layout_id_for`` so equal
    inputs always collide to the same key (key insertion order is irrelevant).
    """
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
