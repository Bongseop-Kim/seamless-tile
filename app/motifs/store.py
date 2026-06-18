"""Motif persistence adapter (Supabase / Postgres via psycopg 3, sync).

The store is the external-dependency seam for motif rows. It is **synchronous** to
match the sync registry hot path (engine compose -> ``get_motif``). The in-memory
``MOTIFS`` dict stays the fast source of truth; this store is hit only at three
points: boot hydration, ``register_motif`` write-through, and an optional cold-miss
lazy load.

Driver = psycopg 3 (sync), one connection per operation against the direct Postgres
DSN (``SUPABASE_DB_URL``) — not the PostgREST REST API. A direct connection is the
shortest path for sync CRUD and is required for native pgvector in S11. Idempotency
= content-hash primary key + ``INSERT ... ON CONFLICT (id) DO NOTHING``.

When unconfigured (no DSN) the default store is ``None`` and every registry call path
treats that as a graceful no-op (NOT an error), so boot and the determinism tests run
with no Supabase env. Explicit callers that go through ``_resolve_store`` get a
``MotifStoreNotConfigured`` (a 502-class ``AdapterClientError``).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from app.adapters.base import AdapterClientError
from app.motifs.registry import MotifDef


@dataclass(frozen=True)
class MotifRecord:
    """Bridges a :class:`MotifDef` (id/symbol/bbox/anchor) with the facet + persistence
    columns. ``MotifDef`` carries no facet metadata, so the record adds it.

    ``color_slots`` is stored now (spec §5.1 column) but P0 motifs are single-color, so
    it defaults to ``["s0"]``; real multi-slot values arrive in S12. ``embedding`` is
    intentionally absent here in P0 (the column exists, nullable, unused — search is S11).
    """

    id: str
    symbol: str
    bbox_mm: tuple[float, float, float, float]
    anchor: tuple[float, float]
    subject: str | None = None
    part: str | None = None
    view: str | None = None
    expression: str | None = None
    style: str | None = None
    description: str | None = None
    tags: list[str] = field(default_factory=list)
    source: str = "recraft"  # 'builtin' | 'llm' | 'recraft'
    status: str = "auto"  # 'auto' | 'curated'
    quality: float | None = None
    variant_group: str | None = None
    color_slots: list[str] = field(default_factory=lambda: ["s0"])

    def to_motif_def(self) -> MotifDef:
        return MotifDef(
            id=self.id,
            symbol=self.symbol,
            bbox_mm=tuple(self.bbox_mm),  # re-tuple in case it arrived as a jsonb list
            anchor=tuple(self.anchor),
        )


@runtime_checkable
class MotifStore(Protocol):
    """CRUD seam. Tests inject an in-memory fake; psycopg is never imported by tests."""

    def upsert(self, record: MotifRecord) -> None:
        """Idempotent insert (ON CONFLICT DO NOTHING). Re-inserting the same id no-ops."""
        ...

    def get(self, motif_id: str) -> MotifRecord | None:
        """Return the row or ``None`` on a clean miss (a miss is NOT an exception)."""
        ...

    def all(self) -> list[MotifRecord]:
        """All rows, for boot hydration."""
        ...


class MotifStoreError(AdapterClientError):
    """The motif store dependency is unavailable or failed (502-class)."""


class MotifStoreNotConfigured(MotifStoreError):
    """No store injected and none configured (no SUPABASE_DB_URL)."""


_DEFAULT_STORE: MotifStore | None = None


def set_default_store(store: MotifStore | None) -> None:
    """Install (or clear) the process-wide default store. Called once at boot."""
    global _DEFAULT_STORE
    _DEFAULT_STORE = store


def get_default_store() -> MotifStore | None:
    """The configured store, or ``None`` when unconfigured (callers no-op gracefully)."""
    return _DEFAULT_STORE


def _resolve_store(store: MotifStore | None) -> MotifStore:
    if store is not None:
        return store
    if _DEFAULT_STORE is not None:
        return _DEFAULT_STORE
    raise MotifStoreNotConfigured(
        "no motif store configured; set SUPABASE_DB_URL or inject via "
        "set_default_store(...). Persistence is opt-in — tests mock the store."
    )


def clear_default_store() -> None:
    """Test helper: drop the process-wide default store (mirrors clear_intent_cache)."""
    set_default_store(None)


# Persisted columns. NOTE: 'embedding' is deliberately omitted from P0 reads/writes
# (the column is nullable and unused until S11).
_COLUMNS = (
    "id",
    "symbol",
    "color_slots",
    "bbox",
    "anchor",
    "subject",
    "part",
    "view",
    "expression",
    "style",
    "description",
    "tags",
    "source",
    "status",
    "quality",
    "variant_group",
)


class PostgresMotifStore:
    """Sync psycopg 3 store. One connection per operation (see module docstring)."""

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn

    def _connect(self):
        # Lazy import so this module loads (and tests collect) without the driver.
        import psycopg

        return psycopg.connect(self._dsn, autocommit=True)

    def upsert(self, record: MotifRecord) -> None:
        try:
            with self._connect() as conn, conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO motifs "
                    "(id, symbol, color_slots, bbox, anchor, subject, part, view, "
                    " expression, style, description, tags, source, status, quality, "
                    " variant_group) "
                    "VALUES (%s, %s, %s::jsonb, %s::jsonb, %s::jsonb, %s, %s, %s, %s, "
                    " %s, %s, %s, %s, %s, %s, %s) "
                    "ON CONFLICT (id) DO NOTHING",
                    (
                        record.id,
                        record.symbol,
                        json.dumps(list(record.color_slots)),
                        json.dumps(list(record.bbox_mm)),
                        json.dumps(list(record.anchor)),
                        record.subject,
                        record.part,
                        record.view,
                        record.expression,
                        record.style,
                        record.description,
                        list(record.tags),
                        record.source,
                        record.status,
                        record.quality,
                        record.variant_group,
                    ),
                )
        except Exception as exc:  # driver / connection failure
            raise MotifStoreError(f"motif upsert failed: {exc}") from exc

    def get(self, motif_id: str) -> MotifRecord | None:
        try:
            with self._connect() as conn, conn.cursor() as cur:
                cur.execute(
                    f"SELECT {', '.join(_COLUMNS)} FROM motifs WHERE id = %s",
                    (motif_id,),
                )
                row = cur.fetchone()
        except Exception as exc:
            raise MotifStoreError(f"motif get failed: {exc}") from exc
        return _row_to_record(row) if row else None

    def all(self) -> list[MotifRecord]:
        try:
            with self._connect() as conn, conn.cursor() as cur:
                cur.execute(f"SELECT {', '.join(_COLUMNS)} FROM motifs ORDER BY id")
                rows = cur.fetchall()
        except Exception as exc:
            raise MotifStoreError(f"motif load failed: {exc}") from exc
        return [_row_to_record(r) for r in rows]


def _row_to_record(row) -> MotifRecord:
    (
        id_,
        symbol,
        color_slots,
        bbox,
        anchor,
        subject,
        part,
        view,
        expression,
        style,
        description,
        tags,
        source,
        status,
        quality,
        variant_group,
    ) = row
    return MotifRecord(
        id=id_,
        symbol=symbol,
        color_slots=list(color_slots) if color_slots else ["s0"],
        bbox_mm=tuple(bbox),
        anchor=tuple(anchor),
        subject=subject,
        part=part,
        view=view,
        expression=expression,
        style=style,
        description=description,
        tags=list(tags or []),
        source=source,
        status=status,
        quality=quality,
        variant_group=variant_group,
    )


def store_from_settings(settings) -> MotifStore | None:
    """Build a :class:`PostgresMotifStore` iff configured, else ``None`` (graceful)."""
    dsn = getattr(settings, "supabase_db_url", None)
    return PostgresMotifStore(dsn) if dsn else None
