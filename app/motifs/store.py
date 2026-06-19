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

    ``color_slots`` is the motif-local slot list (spec §5.1 column); it defaults to
    ``["s0"]`` for single-color motifs and carries the multi-slot values produced by the
    S12 multicolor engine (``normalize_motif_svg``). ``embedding`` is the
    descriptor vector (S11, D12); ``None`` for rows persisted before embeddings existed
    or when no embedding client was configured at generation time.
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
    embedding: list[float] | None = None

    def to_motif_def(self) -> MotifDef:
        return MotifDef(
            id=self.id,
            symbol=self.symbol,
            bbox_mm=tuple(self.bbox_mm),  # re-tuple in case it arrived as a jsonb list
            anchor=tuple(self.anchor),
            color_slots=tuple(self.color_slots),
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

    def find_by_facets(self, subject: str | None, part: str | None) -> list[MotifRecord]:
        """Rows whose controlled facets match (subject, part), ordered by id.

        Empty list == clean miss (NOT an exception), like :meth:`get`. Used by the
        motif resolver's exact-match / hard filter (spec §6.1, P0).
        """
        ...

    def find_by_variant_group(
        self, variant_group: str, *, status: str = "curated"
    ) -> list[MotifRecord]:
        """The sampling pool for a variant_group: rows with the given status, by id.

        Defaults to ``status='curated'`` (only curated variants enter the seed-sampling
        pool, spec §7.4). Empty list == no pool (degenerate; the resolver falls back to
        the matched motif). Used by the variant selection step (spec §7.1, S11).
        """
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


# Persisted columns, in the order `_row_to_record` unpacks them. `embedding` (S11) is
# last; it is a pgvector `vector` column, so reads cast it to text (a stable `'[...]'`
# form parsed by `json.loads`) rather than relying on the default driver representation.
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
    "embedding",
)

# SELECT expression list: same columns/order as `_COLUMNS`, but the pgvector column is
# emitted as `embedding::text` so the round-trip parse in `_row_to_record` is stable.
_SELECT_LIST = ", ".join(
    "embedding::text" if col == "embedding" else col for col in _COLUMNS
)


def _vector_to_text(embedding: list[float] | None) -> str | None:
    """Serialize a vector to pgvector's text input form ``'[a,b,c]'`` (or ``None``)."""
    if embedding is None:
        return None
    return "[" + ",".join(repr(float(x)) for x in embedding) + "]"


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
                    " variant_group, embedding) "
                    "VALUES (%s, %s, %s::jsonb, %s::jsonb, %s::jsonb, %s, %s, %s, %s, "
                    " %s, %s, %s, %s, %s, %s, %s, %s::extensions.vector) "
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
                        _vector_to_text(record.embedding),
                    ),
                )
        except Exception as exc:  # driver / connection failure
            raise MotifStoreError(f"motif upsert failed: {exc}") from exc

    def get(self, motif_id: str) -> MotifRecord | None:
        try:
            with self._connect() as conn, conn.cursor() as cur:
                cur.execute(
                    f"SELECT {_SELECT_LIST} FROM motifs WHERE id = %s",
                    (motif_id,),
                )
                row = cur.fetchone()
        except Exception as exc:
            raise MotifStoreError(f"motif get failed: {exc}") from exc
        return _row_to_record(row) if row else None

    def all(self) -> list[MotifRecord]:
        try:
            with self._connect() as conn, conn.cursor() as cur:
                cur.execute(f"SELECT {_SELECT_LIST} FROM motifs ORDER BY id")
                rows = cur.fetchall()
        except Exception as exc:
            raise MotifStoreError(f"motif load failed: {exc}") from exc
        return [_row_to_record(r) for r in rows]

    def find_by_facets(self, subject: str | None, part: str | None) -> list[MotifRecord]:
        try:
            with self._connect() as conn, conn.cursor() as cur:
                cur.execute(
                    f"SELECT {_SELECT_LIST} FROM motifs "
                    "WHERE subject = %s AND part = %s ORDER BY id",
                    (subject, part),
                )
                rows = cur.fetchall()
        except Exception as exc:
            raise MotifStoreError(f"motif facet query failed: {exc}") from exc
        return [_row_to_record(r) for r in rows]

    def find_by_variant_group(
        self, variant_group: str, *, status: str = "curated"
    ) -> list[MotifRecord]:
        try:
            with self._connect() as conn, conn.cursor() as cur:
                cur.execute(
                    f"SELECT {_SELECT_LIST} FROM motifs "
                    "WHERE variant_group = %s AND status = %s ORDER BY id",
                    (variant_group, status),
                )
                rows = cur.fetchall()
        except Exception as exc:
            raise MotifStoreError(f"motif variant-group query failed: {exc}") from exc
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
        embedding,
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
        # embedding arrives as `embedding::text` ('[a,b,c]', valid JSON) or NULL.
        embedding=json.loads(embedding) if embedding else None,
    )


def store_from_settings(settings) -> MotifStore | None:
    """Build a :class:`PostgresMotifStore` iff configured, else ``None`` (graceful)."""
    dsn = getattr(settings, "supabase_db_url", None)
    return PostgresMotifStore(dsn) if dsn else None
