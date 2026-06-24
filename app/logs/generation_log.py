"""Best-effort generation logging: one row per ``/generate`` request into
``seamless_generation_logs`` (schema owned by the YeongSeon monorepo).

Mirrors the motif store's connection pattern (one psycopg connection per insert
against ``SUPABASE_DB_URL``). It is intentionally best-effort: with no DSN it is a
no-op, and any insert failure is swallowed (logged), so logging can never fail a
generate request. The route schedules it via FastAPI ``BackgroundTasks`` so the DB
round-trip stays off the response hot path.

The row preserves the SVG source + intent/repro metadata that the slimmed API
response no longer returns — this is the system of record for previews and for
deterministic re-export.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

from app.core.config import get_settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class GenerationLogRow:
    request_id: str | None
    input_type: str  # 'intent' | 'prompt' | 'reference_image'
    status: str = "success"  # 'success' | 'partial' | 'error'
    prompt: str | None = None
    has_reference_image: bool = False
    reference_image_bytes: int | None = None
    colorway: str | None = None
    seed: int | None = None
    candidate_count_requested: int | None = None
    candidate_count_returned: int | None = None
    distinct_layouts: int | None = None
    available_strategies: int | None = None
    engine_version: str | None = None
    registry_version: str | None = None
    intent: dict[str, Any] | None = None
    candidates: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    generate_ms: float | None = None
    render_ms: float | None = None


_INSERT = (
    "INSERT INTO seamless_generation_logs "
    "(request_id, input_type, status, prompt, has_reference_image, "
    " reference_image_bytes, colorway, seed, candidate_count_requested, "
    " candidate_count_returned, distinct_layouts, available_strategies, "
    " engine_version, registry_version, intent, candidates, warnings, "
    " generate_ms, render_ms) "
    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, "
    " %s::jsonb, %s::jsonb, %s::jsonb, %s, %s)"
)


def insert_generation_log(row: GenerationLogRow) -> None:
    """Best-effort insert. No-op when ``SUPABASE_DB_URL`` is unset; never raises."""
    dsn = get_settings().supabase_db_url
    if not dsn:
        return
    try:
        import psycopg  # lazy: module loads without the driver (tests)

        with psycopg.connect(dsn, autocommit=True, connect_timeout=5) as conn:
            conn.execute(
                _INSERT,
                (
                    row.request_id,
                    row.input_type,
                    row.status,
                    row.prompt,
                    row.has_reference_image,
                    row.reference_image_bytes,
                    row.colorway,
                    row.seed,
                    row.candidate_count_requested,
                    row.candidate_count_returned,
                    row.distinct_layouts,
                    row.available_strategies,
                    row.engine_version,
                    row.registry_version,
                    json.dumps(row.intent) if row.intent is not None else None,
                    json.dumps(row.candidates),
                    json.dumps(row.warnings),
                    row.generate_ms,
                    row.render_ms,
                ),
            )
    except Exception as exc:  # logging must never break the request
        logger.warning("generation log insert failed: %s", exc)
