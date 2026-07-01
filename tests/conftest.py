"""Test isolation for external services.

Default pytest runs must not write to the shared Supabase project, storage, or
external AI providers. Keep live DB checks opt-in via the ``live_db`` marker.
"""

from __future__ import annotations

import os

import pytest


_LIVE_DB_ENV = "RUN_LIVE_SUPABASE_TESTS"
_DB_ENV = "SUPABASE_DB_URL"
_ALWAYS_BLOCKED_ENV = (
    "SUPABASE_URL",
    "SUPABASE_SERVICE_KEY",
    "GEMINI_API_KEY",
    "OPENAI_API_KEY",
    "RECRAFT_API_KEY",
)


def _live_db_enabled() -> bool:
    return os.environ.get(_LIVE_DB_ENV) == "1"


for _name in _ALWAYS_BLOCKED_ENV:
    os.environ[_name] = ""

if not _live_db_enabled():
    os.environ[_DB_ENV] = ""


@pytest.fixture(autouse=True)
def _block_external_side_effects(request, monkeypatch):
    is_live_db_test = request.node.get_closest_marker("live_db") is not None

    for name in _ALWAYS_BLOCKED_ENV:
        monkeypatch.setenv(name, "")
    if not (_live_db_enabled() and is_live_db_test):
        monkeypatch.setenv(_DB_ENV, "")

    from app.core.config import get_settings
    from app.motifs.store import clear_default_store
    from app.motifs.registry import MOTIFS
    import app.adapters.llm as llm_adapter
    import app.adapters.embedding as emb_adapter
    import app.adapters.recraft as recraft_adapter
    import app.api.routes.generate as generate_route

    def _reset_process_globals() -> None:
        # Superset of the per-suite resets that used to be copy-pasted (and had drifted)
        # across the motif/recraft/chat tests: memoization caches, default clients, the
        # in-memory store, and test-authored ``recraft-`` motifs. A superset can only
        # remove leakage; clearing caches and nulling default clients is inert for the
        # engine suites that never touch them. (test_motif_pool keeps its own broader
        # non-builtin eviction + circle/bee re-seed on top of this.)
        get_settings.cache_clear()
        clear_default_store()
        generate_route.reset_response_cache()
        llm_adapter.clear_intent_cache()
        llm_adapter.set_default_client(None)
        emb_adapter.clear_embedding_cache()
        emb_adapter.set_default_embedding_client(None)
        recraft_adapter.clear_motif_cache()
        recraft_adapter.clear_recraft_motif_cache()
        recraft_adapter.clear_vectorize_cache()
        recraft_adapter.set_default_recraft_client(None)
        for key in [k for k in MOTIFS if k.startswith("recraft-")]:
            del MOTIFS[key]

    _reset_process_globals()
    monkeypatch.setattr(generate_route, "insert_generation_log", lambda row: None)
    monkeypatch.setattr(generate_route, "preview_configured", lambda: False)

    yield

    _reset_process_globals()
