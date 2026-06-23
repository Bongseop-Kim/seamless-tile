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
    import app.api.routes.generate as generate_route

    get_settings.cache_clear()
    clear_default_store()
    monkeypatch.setattr(generate_route, "insert_generation_log", lambda row: None)
    monkeypatch.setattr(generate_route, "preview_configured", lambda: False)

    yield

    clear_default_store()
    get_settings.cache_clear()
