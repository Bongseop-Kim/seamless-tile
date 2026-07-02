"""Best-effort mirror of a committed session into ``seamless_sessions`` (schema owned by
the YeongSeon monorepo, session 17 §11/S7).

Mirrors ``app.logs.generation_log``'s pattern exactly: one psycopg connection per write
against ``SUPABASE_DB_URL``, no-op without a DSN, and any failure is logged and swallowed
-- this row is a reporting mirror for the monorepo UI, never the source of truth. The
source of truth for session restore is the LangGraph checkpointer itself
(``app.sessions.graph.get_state``), which is why this module has no read method.
"""

from __future__ import annotations

import json
import logging

from app.core.config import get_settings

logger = logging.getLogger(__name__)

_UPSERT = (
    "INSERT INTO seamless_sessions "
    "(thread_id, status, seed, colorway, registry_version, current_intent) "
    "VALUES (%s, %s, %s, %s, %s, %s::jsonb) "
    "ON CONFLICT (thread_id) DO UPDATE SET "
    "status = EXCLUDED.status, seed = EXCLUDED.seed, colorway = EXCLUDED.colorway, "
    "registry_version = EXCLUDED.registry_version, "
    "current_intent = EXCLUDED.current_intent"  # updated_at: schema trigger owns it
)


def upsert_session_row(
    *,
    thread_id: str,
    status: str = "active",
    seed: int | None = None,
    colorway: str | None = None,
    registry_version: str | None = None,
    current_intent: dict | None = None,
) -> None:
    """Best-effort insert/update. No-op when ``SUPABASE_DB_URL`` is unset; never raises."""
    dsn = get_settings().supabase_db_url
    if not dsn:
        return
    try:
        import psycopg  # lazy: module loads without the driver (tests)

        with psycopg.connect(dsn, autocommit=True, connect_timeout=5) as conn:
            conn.execute(
                _UPSERT,
                (
                    thread_id,
                    status,
                    seed,
                    colorway,
                    registry_version,
                    json.dumps(current_intent) if current_intent is not None else None,
                ),
            )
    except Exception as exc:  # a reporting mirror must never break a session turn
        logger.warning("session row upsert failed: %s", exc)
