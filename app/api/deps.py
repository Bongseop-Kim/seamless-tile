"""In-memory pattern store (id -> Pattern).

MVP-only: contents are lost on restart and not shared across worker processes.
Swap this module for a durable store when persistence is required.
"""

from app.domain.pattern import Pattern

_STORE: dict[str, Pattern] = {}


def get_store() -> dict[str, Pattern]:
    return _STORE
