"""Deterministic session cost guard (session 17, S13): pure budget-ceiling checks and an
in-flight dedup lock. LLM-independent and counter-based -- no wall clock, no randomness,
so the guard itself never breaks the "same input -> same output" contract.
"""

from __future__ import annotations

import threading
from contextlib import contextmanager

from app.core.config import get_settings


def budget_exceeded(budget: dict | None, kind: str) -> str | None:
    """``None`` if ``{kind}_used`` is under the configured ceiling, else a rejection
    message. Pure: settings + the counter already on the session state, nothing else.
    ``kind`` maps to the ``session_{kind}_limit`` setting by naming convention."""
    limit = getattr(get_settings(), f"session_{kind}_limit")
    used = (budget or {}).get(f"{kind}_used", 0)
    if used >= limit:
        return f"session {kind} budget exhausted ({used}/{limit})"
    return None


class SessionBusy(Exception):
    """The session already has a graph operation in flight (dedup, S13)."""


_INFLIGHT: set[str] = set()
_LOCKS: dict[str, threading.RLock] = {}
_MUTEX = threading.Lock()


def _lock_for(session_id: str) -> threading.RLock:
    with _MUTEX:
        lock = _LOCKS.get(session_id)
        if lock is None:
            lock = threading.RLock()
            _LOCKS[session_id] = lock
        return lock


@contextmanager
def session_critical(session_id: str):
    """Serialize state read-modify-write blocks for a session without changing 409 policy."""
    with _lock_for(session_id):
        yield


@contextmanager
def session_inflight(session_id: str):
    """Process-local in-flight lock keyed by ``session_id``: a session runs one graph
    operation at a time, so a duplicate confirm/generate (double-click, retry race) can
    never fire Recraft twice. Released on any exit, including an error.

    # ponytail: process-local lock (a `set` + `threading.Lock`) -- a multi-worker
    # deployment needs a shared lock (e.g. a Postgres advisory lock); single worker today.
    """
    lock = _lock_for(session_id)
    with _MUTEX:
        if session_id in _INFLIGHT:
            raise SessionBusy(session_id)
        _INFLIGHT.add(session_id)
    try:
        with lock:
            yield
    finally:
        with _MUTEX:
            _INFLIGHT.discard(session_id)


def reset_inflight() -> None:
    """Test isolation: drop all in-flight locks."""
    with _MUTEX:
        _INFLIGHT.clear()
