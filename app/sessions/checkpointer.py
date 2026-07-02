"""LangGraph checkpointer backend selection (session 17, S7/S14).

``SUPABASE_DB_URL`` unset => ``MemorySaver`` (session 16 in-memory degrade, byte-identical).
Set => ``PostgresSaver`` over the ``checkpoint*`` tables the YeongSeon monorepo migration
pre-defines. This module **never runs the ``PostgresSaver`` schema-setup step** (it issues
DDL, which this repo is forbidden from executing, CLAUDE.md/S14) -- ``_probe`` is a read-only
readiness check that fails clean (``SessionStoreError``, 502-class) instead of
self-provisioning when the tables are missing or the schema is behind the pinned
``langgraph-checkpoint-postgres`` version.
"""

from __future__ import annotations

from app.adapters.base import AdapterClientError

_REQUIRED_TABLES = (
    "checkpoints",
    "checkpoint_blobs",
    "checkpoint_writes",
    "checkpoint_migrations",
    "seamless_sessions",
)


class SessionStoreError(AdapterClientError):
    """Session persistence backend (Postgres checkpointer) unavailable or misconfigured
    (502-class) -- e.g. the monorepo migration has not been applied yet."""


def _probe(dsn: str) -> None:
    """Read-only readiness check: all required tables exist and ``checkpoint_migrations``
    is at (or past) the pinned ``langgraph-checkpoint-postgres`` version. Never creates or
    alters anything -- a missing table or stale version is a clean client error, not a
    trigger to self-provision (acceptance #7)."""
    import psycopg
    from langgraph.checkpoint.postgres.base import BasePostgresSaver

    expected = len(BasePostgresSaver.MIGRATIONS) - 1
    try:
        with psycopg.connect(dsn, autocommit=True, connect_timeout=5) as conn:
            conn.execute("SELECT 1 FROM " + ", ".join(_REQUIRED_TABLES) + " LIMIT 0")
            row = conn.execute("SELECT max(v) FROM checkpoint_migrations").fetchone()
    except psycopg.errors.UndefinedTable as exc:
        raise SessionStoreError(
            f"session persistence tables missing ({exc}); apply the YeongSeon monorepo "
            "migration before enabling SUPABASE_DB_URL -- this app never runs DDL "
            "(CLAUDE.md/S14)"
        ) from exc
    except Exception as exc:  # driver / connection failure
        raise SessionStoreError(f"session store unreachable: {exc}") from exc
    version = row[0] if row else None
    if version is None or version < expected:
        raise SessionStoreError(
            f"checkpoint_migrations is at v{version}, but the pinned "
            f"langgraph-checkpoint-postgres==3.1.0 needs v{expected}; update the "
            "monorepo migration mirror (session 17 bootstrap note)"
        )


_POOL = None


def checkpointer_from_settings(settings):
    """``PostgresSaver`` iff ``SUPABASE_DB_URL`` is set (tables pre-exist -- ``setup()`` is
    NEVER called), else ``MemorySaver`` (session-16 in-memory degrade)."""
    dsn = settings.supabase_db_url
    if not dsn:
        from langgraph.checkpoint.memory import MemorySaver

        return MemorySaver()
    _probe(dsn)
    from psycopg.rows import dict_row
    from psycopg_pool import ConnectionPool
    from langgraph.checkpoint.postgres import PostgresSaver

    global _POOL
    _POOL = ConnectionPool(
        dsn,
        min_size=1,
        max_size=4,
        open=True,
        # prepare_threshold=None disables server-side prepared statements -- needed if the
        # DSN is a Supabase pgbouncer pooler (6543, transaction mode), which does not
        # support them. Direct connections (5432) tolerate this setting too.
        kwargs={"autocommit": True, "row_factory": dict_row, "prepare_threshold": None},
    )
    return PostgresSaver(_POOL)


def close_checkpointer() -> None:
    """Close and drop the process-wide connection pool (test isolation / ops)."""
    global _POOL
    if _POOL is not None:
        _POOL.close()
        _POOL = None
