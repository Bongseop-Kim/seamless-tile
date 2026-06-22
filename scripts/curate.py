"""Curate persisted motifs: list the review queue, promote to curated, or reject (manual).

Session 14 (P3) Tier2 review tool. Promotion (``status`` 'auto' -> 'curated') is what
admits a motif into the curated seed-sampling pool (spec §7.4); rejection deletes the
row and flushes the in-memory registry + adapter caches (spec §6.4). NOTE: the cache
flush is process-local — it clears THIS CLI process's caches, not those of a separately
running API server. After a reject, restart the server (or otherwise invalidate its
caches) so a warm cache cannot re-serve the deleted motif. Curation is an explicit
operator action, so failures here are loud (unlike the best-effort write-through used at
authoring time).

Needs SUPABASE_DB_URL in the environment — server-side only, the direct connection
bypasses RLS (CLAUDE.md). This is an operator tool, NOT part of the test suite.

Usage:
    .venv/bin/python scripts/curate.py list                  # pending (status=auto)
    .venv/bin/python scripts/curate.py list --status curated
    .venv/bin/python scripts/curate.py promote <motif_id>
    .venv/bin/python scripts/curate.py reject  <motif_id>
"""

from __future__ import annotations

import argparse
import os
import sys

# Allow running from the repo root without installing the package.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.config import get_settings  # noqa: E402
from app.motifs.registry import promote_motif, reject_motif  # noqa: E402
from app.motifs.store import (  # noqa: E402
    MotifStoreError,
    set_default_store,
    store_from_settings,
)


def _require_store():
    """Build the configured store and install it as the default (so reject/promote
    resolve the same store), or return ``None`` with a clear message when unconfigured."""
    store = store_from_settings(get_settings())
    if store is None:
        print(
            "SUPABASE_DB_URL is not set — cannot reach the motif store.",
            file=sys.stderr,
        )
        return None
    set_default_store(store)
    return store


def _cmd_list(store, status: str) -> int:
    rows = store.find_by_status(status)
    if not rows:
        print(f"(no motifs with status={status!r})")
        return 0
    print(f"{len(rows)} motif(s) with status={status!r}:")
    print(f"{'id':<28} {'subject':<14} {'scope':<8} {'source':<8} variant_group")
    for r in rows:
        print(
            f"{r.id:<28} {(r.subject or '-'):<14} {(r.scope or '-'):<8} "
            f"{r.source:<8} {r.variant_group or '-'}"
        )
    return 0


def _cmd_promote(store, motif_id: str) -> int:
    if store.get(motif_id) is None:
        print(f"motif {motif_id!r} not found.", file=sys.stderr)
        return 1
    promote_motif(motif_id)
    print(f"promoted {motif_id} -> curated")
    return 0


def _cmd_reject(store, motif_id: str) -> int:
    if store.get(motif_id) is None:
        print(f"motif {motif_id!r} not found.", file=sys.stderr)
        return 1
    try:
        reject_motif(motif_id)
    except ValueError as exc:  # built-in guard (circle/bee)
        print(str(exc), file=sys.stderr)
        return 1
    print(
        f"rejected {motif_id} (deleted from store; this process's in-memory + adapter "
        "caches flushed — restart any running API server to clear its caches)"
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_list = sub.add_parser("list", help="list motifs by status (default: auto)")
    p_list.add_argument(
        "--status",
        choices=["auto", "curated"],
        default="auto",
        help="status to list (default: auto)",
    )

    p_promote = sub.add_parser("promote", help="promote a motif to curated")
    p_promote.add_argument("motif_id")

    p_reject = sub.add_parser("reject", help="delete a motif everywhere")
    p_reject.add_argument("motif_id")

    args = parser.parse_args()

    store = _require_store()
    if store is None:
        return 2

    try:
        if args.command == "list":
            return _cmd_list(store, args.status)
        if args.command == "promote":
            return _cmd_promote(store, args.motif_id)
        if args.command == "reject":
            return _cmd_reject(store, args.motif_id)
    except MotifStoreError as exc:
        print(f"store error: {exc}", file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
