"""Seed a small, high-quality 'head' motif catalog as reusable rows.

The head catalog is the popular-spec set pre-seeded at high quality so the sampling pool
is non-degenerate from day one. Each entry enters the seed-sampling pool immediately;
content-hash ids + ON CONFLICT DO NOTHING make re-running the script idempotent.

To exercise variant diversity (pool >= 2), several entries share the same
(subject, scope) -> the same variant_group, so one spec resolves to different variants
per seed (spec §7.1).

Needs SUPABASE_DB_URL (server-side only; bypasses RLS — CLAUDE.md). The runtime never
runs DDL: the `motifs` table/indexes are owned by the React monorepo (CLAUDE.md); this
script only INSERTs rows. Operator tool, NOT part of the test suite.

Usage:
    .venv/bin/python scripts/seed_head_catalog.py
"""

from __future__ import annotations

import os
import sys

# Allow running from the repo root without installing the package.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.config import get_settings  # noqa: E402
from app.motifs.registry import normalize_motif_svg, register_motif  # noqa: E402
from app.motifs.store import set_default_store, store_from_settings  # noqa: E402


def _svg(inner: str) -> str:
    return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">{inner}</svg>'


# Each entry: subject (free text) + scope (controlled facet -> variant_group) + a free-text
# descriptor + the motif SVG. Entries sharing (subject, scope) form one variant pool;
# `flower/whole` and `leaf/whole` each carry >= 2 variants to demonstrate pool >= 2.
HEAD_CATALOG: list[dict] = [
    {
        "subject": "flower",
        "scope": "whole",
        "description": "five-petal flower, flat",
        "style": "flat",
        "svg": _svg(
            '<circle cx="50" cy="28" r="14" fill="#e57373"/>'
            '<circle cx="72" cy="44" r="14" fill="#e57373"/>'
            '<circle cx="63" cy="71" r="14" fill="#e57373"/>'
            '<circle cx="37" cy="71" r="14" fill="#e57373"/>'
            '<circle cx="28" cy="44" r="14" fill="#e57373"/>'
            '<circle cx="50" cy="50" r="12" fill="#e57373"/>'
        ),
    },
    {
        "subject": "flower",
        "scope": "whole",
        "description": "four-petal flower, flat",
        "style": "flat",
        "svg": _svg(
            '<ellipse cx="50" cy="30" rx="12" ry="20" fill="#ba68c8"/>'
            '<ellipse cx="50" cy="70" rx="12" ry="20" fill="#ba68c8"/>'
            '<ellipse cx="30" cy="50" rx="20" ry="12" fill="#ba68c8"/>'
            '<ellipse cx="70" cy="50" rx="20" ry="12" fill="#ba68c8"/>'
            '<circle cx="50" cy="50" r="10" fill="#ba68c8"/>'
        ),
    },
    {
        "subject": "flower",
        "scope": "whole",
        "description": "six-petal flower, flat",
        "style": "flat",
        "svg": _svg(
            '<circle cx="50" cy="26" r="12" fill="#4db6ac"/>'
            '<circle cx="71" cy="38" r="12" fill="#4db6ac"/>'
            '<circle cx="71" cy="62" r="12" fill="#4db6ac"/>'
            '<circle cx="50" cy="74" r="12" fill="#4db6ac"/>'
            '<circle cx="29" cy="62" r="12" fill="#4db6ac"/>'
            '<circle cx="29" cy="38" r="12" fill="#4db6ac"/>'
            '<circle cx="50" cy="50" r="11" fill="#4db6ac"/>'
        ),
    },
    {
        "subject": "leaf",
        "scope": "whole",
        "description": "pointed leaf, flat",
        "style": "flat",
        "svg": _svg('<path d="M50 10 C20 40 20 70 50 90 C80 70 80 40 50 10 Z" fill="#66bb6a"/>'),
    },
    {
        "subject": "leaf",
        "scope": "whole",
        "description": "rounded leaf, flat",
        "style": "flat",
        "svg": _svg('<path d="M20 50 C40 20 70 20 85 50 C70 80 40 80 20 50 Z" fill="#81c784"/>'),
    },
]


def seed(store) -> list[str]:
    """Register every catalog entry as a reusable motif; return the motif ids.

    Installs ``store`` as the default so ``register_motif``'s write-through persists to
    it. Idempotent: the same SVG always hashes to the same id, and upsert is
    ON CONFLICT DO NOTHING.
    """
    set_default_store(store)
    ids: list[str] = []
    for entry in HEAD_CATALOG:
        motif = normalize_motif_svg(entry["svg"])
        motif_id = register_motif(
            motif,
            subject=entry["subject"],
            scope=entry["scope"],
            description=entry.get("description"),
            style=entry.get("style"),
            source="seed",
        )
        ids.append(motif_id)
    return ids


def main() -> int:
    store = store_from_settings(get_settings())
    if store is None:
        print("SUPABASE_DB_URL is not set — cannot seed the motif store.", file=sys.stderr)
        return 2
    ids = seed(store)
    print(f"seeded {len(ids)} reusable motif(s):")
    for entry, motif_id in zip(HEAD_CATALOG, ids):
        print(f"  {motif_id}  {entry['subject']}/{entry['scope']}  {entry['description']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
