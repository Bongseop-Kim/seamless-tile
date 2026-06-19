"""Measure the Recraft suitability-gate sanitize pass-rate (spec §12, acceptance #4).

This is a **manual ops tool**, NOT part of the test suite (it would need live Recraft
output / a real sample directory). It runs the same gate the runtime uses
(``_flatten_unsuitable`` -> ``normalize_motif_svg(max_color_slots=...)``) over a folder
of ``*.svg`` samples and reports how many pass, so the §12 ``Y%`` baseline and the slot
cap ``N`` can be calibrated against real Recraft output.

Usage:
    .venv/bin/python scripts/measure_recraft_passrate.py path/to/recraft_svgs [--max-slots 6]

Populate the folder with raw SVGs from the real Recraft API (e.g. a batch of detailed
motif prompts) and inspect the per-file PASS/FAIL breakdown.
"""

from __future__ import annotations

import argparse
import glob
import os
import sys

# Allow running from the repo root without installing the package.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.adapters.recraft import _flatten_unsuitable  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.motifs.registry import normalize_motif_svg  # noqa: E402
from app.render.sanitize import SanitizeError  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("samples_dir", help="folder of raw Recraft *.svg samples")
    parser.add_argument(
        "--max-slots",
        type=int,
        default=get_settings().recraft_max_color_slots,
        help="color-slot cap (default: recraft_max_color_slots from config)",
    )
    args = parser.parse_args()

    paths = sorted(glob.glob(os.path.join(args.samples_dir, "*.svg")))
    if not paths:
        print(f"no *.svg files in {args.samples_dir!r}", file=sys.stderr)
        return 2

    passed = 0
    for path in paths:
        with open(path, encoding="utf-8") as fh:
            raw = fh.read()
        try:
            motif = normalize_motif_svg(
                _flatten_unsuitable(raw), max_color_slots=args.max_slots
            )
            passed += 1
            print(f"PASS  {os.path.basename(path)}  slots={len(motif.color_slots)}")
        except (SanitizeError, ValueError) as exc:
            print(f"FAIL  {os.path.basename(path)}  {exc}")

    rate = passed / len(paths)
    print(f"\npass-rate: {passed}/{len(paths)} = {rate:.1%}  (max_slots={args.max_slots})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
