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
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Protocol

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
    subject: str | None = None  # free text (no controlled vocab; D10)
    scope: str | None = None  # controlled: 'whole' | 'partial' (D10)
    view: str | None = None
    expression: str | None = None
    style: str | None = None
    description: str | None = None
    tags: list[str] = field(default_factory=list)
    source: str = "recraft"  # 'builtin' | 'llm' | 'recraft'
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


@dataclass(frozen=True)
class MotifMeta:
    """A search candidate without the symbol/embedding payload (spec §6.1, #4).

    The resolver's exact-match and lowest-id fallback only read facets + ids, so the
    candidate scan never transfers the SVG symbol or the 1536-d embedding. ``description``
    is a facet (it carries the part/anatomy name, D10) and is part of the exact-descriptor
    comparison, so it travels with the meta row — still far cheaper than the payload.
    """

    id: str
    variant_group: str | None
    subject: str | None
    scope: str | None
    view: str | None
    expression: str | None
    style: str | None
    description: str | None = None


@dataclass(frozen=True)
class MotifMatch:
    """The single best embedding-similarity hit (id + group + cosine similarity)."""

    id: str
    variant_group: str | None
    similarity: float


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

    def all_ids(self) -> list[str]:
        """All motif ids, ordered by id. Cheap id-only scan for the reusable-pool
        fingerprint (avoids loading symbol/embedding payloads)."""
        ...

    def find_facets_meta(self, scope: str | None) -> list[MotifMeta]:
        """Facet metadata (no symbol/embedding) for ``scope``, ordered by id.

        Empty list == clean miss (NOT an exception), like :meth:`get`. Feeds the motif
        resolver's exact-match and lowest-id fallback (spec §6.1); ``scope`` is the only
        controlled facet. Cosine ranking is pushed to :meth:`find_best_by_embedding`.
        """
        ...

    def find_best_by_embedding(
        self, scope: str, query_vec: list[float]
    ) -> MotifMatch | None:
        """The closest motif in ``scope`` by cosine similarity, or ``None``.

        Ranks with pgvector's ``<=>`` (cosine distance) in Postgres instead of pulling
        embeddings into Python (#3). ``None`` means no comparable row (all NULL or a
        different dimension) — the resolver then falls back like the old soft-similarity
        miss. The τ gate stays in the resolver, applied to ``similarity``.
        """
        ...

    def find_by_variant_group(self, variant_group: str) -> list[MotifRecord]:
        """The sampling pool for a variant_group, ordered by id.

        Empty list == no pool; the resolver falls back to the matched motif. Used by
        the variant selection step (spec §7.1, S11).
        """
        ...

    def delete(self, motif_id: str) -> None:
        """Remove a motif row (admin cleanup, spec §6.4). A no-op when absent."""
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


def _resolve_store() -> MotifStore:
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
    "scope",
    "view",
    "expression",
    "style",
    "description",
    "tags",
    "source",
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


def _facet_where_clause(scope: str | None) -> tuple[str, tuple[str, ...]]:
    if scope is None:
        return "scope IS NULL", ()
    return "scope = %s", (scope,)


# Search candidates need only facets + ids (no symbol/embedding payload, #4).
_META_SELECT = "id, variant_group, subject, scope, view, expression, style, description"


def _row_to_meta(row) -> MotifMeta:
    id_, variant_group, subject, scope, view, expression, style, description = row
    return MotifMeta(
        id=id_,
        variant_group=variant_group,
        subject=subject,
        scope=scope,
        view=view,
        expression=expression,
        style=style,
        description=description,
    )


class PostgresMotifStore:
    """Sync psycopg 3 store. One connection per operation (see module docstring)."""

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn

    def _connect(self):
        # Lazy import so this module loads (and tests collect) without the driver.
        import psycopg

        return psycopg.connect(self._dsn, autocommit=True, connect_timeout=5)

    @contextmanager
    def _cursor(self, op: str):
        """Yield a cursor for one operation, wrapping any driver/connection failure as
        a ``MotifStoreError`` tagged with ``op`` ("motif <op> failed: ...")."""
        try:
            with self._connect() as conn, conn.cursor() as cur:
                yield cur
        except Exception as exc:  # driver / connection failure
            raise MotifStoreError(f"motif {op} failed: {exc}") from exc

    def upsert(self, record: MotifRecord) -> None:
        with self._cursor("upsert") as cur:
            cur.execute(
                "INSERT INTO motifs "
                "(id, symbol, color_slots, bbox, anchor, subject, scope, view, "
                " expression, style, description, tags, source, quality, "
                " variant_group, embedding) "
                "VALUES (%s, %s, %s::jsonb, %s::jsonb, %s::jsonb, %s, %s, %s, %s, "
                " %s, %s, %s, %s, %s, %s, %s::extensions.vector) "
                "ON CONFLICT (id) DO NOTHING",
                (
                    record.id,
                    record.symbol,
                    json.dumps(list(record.color_slots)),
                    json.dumps(list(record.bbox_mm)),
                    json.dumps(list(record.anchor)),
                    record.subject,
                    record.scope,
                    record.view,
                    record.expression,
                    record.style,
                    record.description,
                    list(record.tags),
                    record.source,
                    record.quality,
                    record.variant_group,
                    _vector_to_text(record.embedding),
                ),
            )

    def get(self, motif_id: str) -> MotifRecord | None:
        with self._cursor("get") as cur:
            cur.execute(
                f"SELECT {_SELECT_LIST} FROM motifs WHERE id = %s",
                (motif_id,),
            )
            row = cur.fetchone()
        return _row_to_record(row) if row else None

    def all(self) -> list[MotifRecord]:
        with self._cursor("load") as cur:
            cur.execute(f"SELECT {_SELECT_LIST} FROM motifs ORDER BY id")
            rows = cur.fetchall()
        return [_row_to_record(r) for r in rows]

    def all_ids(self) -> list[str]:
        with self._cursor("id scan") as cur:
            cur.execute("SELECT id FROM motifs ORDER BY id")
            rows = cur.fetchall()
        return [r[0] for r in rows]

    def find_facets_meta(self, scope: str | None) -> list[MotifMeta]:
        with self._cursor("facet query") as cur:
            where, params = _facet_where_clause(scope)
            cur.execute(
                f"SELECT {_META_SELECT} FROM motifs WHERE {where} ORDER BY id",
                params,
            )
            rows = cur.fetchall()
        return [_row_to_meta(r) for r in rows]

    def find_best_by_embedding(
        self, scope: str, query_vec: list[float]
    ) -> MotifMatch | None:
        with self._cursor("embedding query") as cur:
            # `<=>` is cosine distance; similarity = 1 - distance. ORDER BY distance,
            # id keeps the lowest-id tie-break of the old Python scan. The vector_dims
            # filter mirrors the resolver's len(emb) != len(query) guard — a no-op once
            # the column is fixed-dim (monorepo #2). `extensions.`-qualified so it
            # resolves regardless of search_path.
            cur.execute(
                "SELECT id, variant_group, "
                "  1 - (embedding <=> %(q)s::extensions.vector) AS similarity "
                "FROM motifs "
                "WHERE scope = %(scope)s "
                "  AND embedding IS NOT NULL "
                "  AND extensions.vector_dims(embedding) = %(dim)s "
                "ORDER BY embedding <=> %(q)s::extensions.vector ASC, id ASC "
                "LIMIT 1",
                {
                    "q": _vector_to_text(query_vec),
                    "scope": scope,
                    "dim": len(query_vec),
                },
            )
            row = cur.fetchone()
        if row is None:
            return None
        return MotifMatch(id=row[0], variant_group=row[1], similarity=float(row[2]))

    def find_by_variant_group(self, variant_group: str) -> list[MotifRecord]:
        with self._cursor("variant-group query") as cur:
            cur.execute(
                f"SELECT {_SELECT_LIST} FROM motifs "
                "WHERE variant_group = %s ORDER BY id",
                (variant_group,),
            )
            rows = cur.fetchall()
        return [_row_to_record(r) for r in rows]

    def delete(self, motif_id: str) -> None:
        with self._cursor("delete") as cur:
            cur.execute("DELETE FROM motifs WHERE id = %s", (motif_id,))


def _row_to_record(row) -> MotifRecord:
    (
        id_,
        symbol,
        color_slots,
        bbox,
        anchor,
        subject,
        scope,
        view,
        expression,
        style,
        description,
        tags,
        source,
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
        scope=scope,
        view=view,
        expression=expression,
        style=style,
        description=description,
        tags=list(tags or []),
        source=source,
        quality=quality,
        variant_group=variant_group,
        # embedding arrives as `embedding::text` ('[a,b,c]', valid JSON) or NULL.
        embedding=json.loads(embedding) if embedding else None,
    )


def store_from_settings(settings) -> MotifStore | None:
    """Build a :class:`PostgresMotifStore` iff configured, else ``None`` (graceful)."""
    dsn = getattr(settings, "supabase_db_url", None)
    return PostgresMotifStore(dsn) if dsn else None
