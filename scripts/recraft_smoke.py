"""Call the REAL Recraft vector API and inspect the SVG + suitability gate (manual).

Needs RECRAFT_API_KEY in .env (plus optional RECRAFT_* overrides). It generates a vector
SVG for a prompt, saves the RAW SVG, runs the same suitability gate the runtime uses
(``_flatten_unsuitable`` -> ``normalize_motif_svg(max_color_slots=N)``), saves the
FLATTENED SVG, and prints the motif id / color slots / pass-fail and byte sizes. This is
a developer tool — NOT part of the (offline, deterministic) test suite.

Usage:
    .venv/bin/python scripts/recraft_smoke.py "a flat vector pig face, minimal, 3 colors"
    .venv/bin/python scripts/recraft_smoke.py "..." --out recraft_out --b64
    .venv/bin/python scripts/recraft_smoke.py "..." --model recraftv4_vector

Outputs land in --out (default ./recraft_out), which is gitignored.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import time

# Allow running from the repo root without installing the package.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.adapters.recraft import _flatten_unsuitable, client_from_settings  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.motifs.registry import normalize_motif_svg  # noqa: E402
from app.render.sanitize import SanitizeError  # noqa: E402


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40] or "motif"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("prompt", help="text prompt for the vector motif")
    parser.add_argument("--out", default="recraft_out", help="output dir (default: recraft_out)")
    parser.add_argument("--model", default=None, help="override RECRAFT_MODEL (e.g. recraftv4_vector)")
    parser.add_argument("--b64", action="store_true", help="use response_format=b64_json (inline SVG)")
    args = parser.parse_args()

    settings = get_settings()
    client = client_from_settings(settings)
    if client is None:
        print("RECRAFT_API_KEY is not set in .env — cannot call the API.", file=sys.stderr)
        return 2
    # Apply CLI overrides on the built client (it reads its own fields).
    if args.model:
        client._model = args.model  # noqa: SLF001 (dev tool)
    if args.b64:
        client._response_format = "b64_json"  # noqa: SLF001

    print(f"prompt   : {args.prompt}")
    print(f"model    : {client._model}  style={client._style}  format={client._response_format}")
    print("calling Recraft ...", flush=True)
    t0 = time.perf_counter()
    raw = client.generate(args.prompt)
    print(f"received : {len(raw)} bytes in {time.perf_counter() - t0:.1f}s")

    os.makedirs(args.out, exist_ok=True)
    stamp = f"{_slug(args.prompt)}-{int(time.time())}"
    raw_path = os.path.join(args.out, f"{stamp}.raw.svg")
    with open(raw_path, "w", encoding="utf-8") as fh:
        fh.write(raw)
    print(f"raw SVG  : {raw_path}")

    cap = settings.recraft_max_color_slots
    try:
        flat = _flatten_unsuitable(raw)
        motif = normalize_motif_svg(flat, max_color_slots=cap)
    except (SanitizeError, ValueError) as exc:
        print(f"GATE FAIL: {exc}")
        print("(raw SVG saved above for inspection)")
        return 1

    flat_path = os.path.join(args.out, f"{stamp}.flat.svg")
    with open(flat_path, "w", encoding="utf-8") as fh:
        fh.write(flat)
    print(f"flat SVG : {flat_path}  ({len(flat)} bytes)")
    print(f"GATE PASS: motif_id={motif.id}  color_slots={list(motif.color_slots)} (cap {cap})")
    print(f"symbol   : {motif.symbol[:200]}{'...' if len(motif.symbol) > 200 else ''}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
