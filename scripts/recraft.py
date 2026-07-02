#!/usr/bin/env python3
"""Manual Recraft operator tools.

Subcommands:
  smoke     Call the real Recraft vector API, save raw/flattened SVG, run the gate.
  passrate  Measure gate pass-rate over a directory of raw Recraft SVG samples.
  tile      Generate a Recraft motif and wrap it in a deterministic seamless tile.

Live API commands require RECRAFT_API_KEY. This file is not part of the runtime path.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.adapters.recraft import (  # noqa: E402
    _gate_motif_svg,
    client_from_settings,
)
from app.core.config import get_settings  # noqa: E402
from app.engine.generate import generate  # noqa: E402
from app.motifs.registry import get_motif, register_motif  # noqa: E402
from app.render.raster import RasterError, find_renderer, rasterize  # noqa: E402
from app.render.sanitize import SanitizeError  # noqa: E402

_PALETTE = ["#ef9aa6", "#2e2a2a", "#fbf3e0", "#2f8f83", "#e8b23a", "#c8553d"]
_BG_HEX = "#f5efe3"
_DEFAULT_PROMPT = (
    "a single pig riding a bicycle, flat vector illustration, bold simple shapes, "
    "centered, no background"
)


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40] or "motif"


def _client_or_exit():
    client = client_from_settings(get_settings())
    if client is None:
        raise SystemExit("RECRAFT_API_KEY is not set in .env -- cannot call the API.")
    return client


def build_tile_intent(
    motif_id: str,
    *,
    tile_mm: float = 48.0,
    cell_mm: float = 24.0,
    size_mm: float = 18.0,
    drop_fraction: float = 0.5,
    palette_hex: list[str] | None = None,
    bg_hex: str = _BG_HEX,
    seed: int = 7,
) -> dict:
    """Wrap a registered motif in a seamless half-drop lattice intent."""
    slots = list(get_motif(motif_id).color_slots)
    palette_hex = palette_hex or _PALETTE
    palette_slots = [{"id": "bg", "hex": bg_hex}]
    mapping = {"bg": bg_hex}
    colors: dict[str, str] = {}
    for i, slot in enumerate(slots):
        pid, hex_ = f"p{i}", palette_hex[i % len(palette_hex)]
        palette_slots.append({"id": pid, "hex": hex_})
        mapping[pid] = hex_
        colors[slot] = pid
    return {
        "intent_version": 1,
        "canvas": {"tile_mm": tile_mm, "dpi": 300},
        "seed": seed,
        "production": {"method": "print", "max_colors": 12},
        "palette": {"slots": palette_slots},
        "colorways": [{"id": "default", "name": "default", "mapping": mapping}],
        "layers": [
            {"id": "bg", "type": "background", "z_order": 0, "params": {"color": "bg"}},
            {
                "id": "motif",
                "type": "motif",
                "z_order": 1,
                "params": {"motif_id": motif_id, "size_mm": size_mm, "colors": colors},
                "placement": {
                    "type": "lattice",
                    "lattice": {
                        "cell_w_mm": cell_mm,
                        "cell_h_mm": cell_mm,
                        "drop_fraction": drop_fraction,
                        "drop_axis": "row",
                    },
                },
            },
        ],
    }


def _gate(raw: str, max_slots: int, **kwargs):
    return _gate_motif_svg(raw, max_color_slots=max_slots, **kwargs)


def cmd_smoke(args) -> int:
    client = _client_or_exit()
    if args.model:
        client._model = args.model  # noqa: SLF001 - operator override
    if args.b64:
        client._response_format = "b64_json"  # noqa: SLF001 - operator override

    print(f"prompt   : {args.prompt}")
    print(f"model    : {client._model}  style={client._style}  format={client._response_format}")
    print("calling Recraft ...", flush=True)
    started = time.perf_counter()
    try:
        raw = client.generate(args.prompt)
    except Exception as exc:  # noqa: BLE001 - CLI reports upstream failures plainly
        print(f"Recraft API call failed: {exc}", file=sys.stderr)
        return 1
    print(f"received : {len(raw)} bytes in {time.perf_counter() - started:.1f}s")

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    stamp = f"{_slug(args.prompt)}-{time.time_ns()}"
    raw_path = out / f"{stamp}.raw.svg"
    raw_path.write_text(raw, encoding="utf-8")
    print(f"raw SVG  : {raw_path}")

    try:
        flat, motif = _gate(raw, get_settings().recraft_max_color_slots)
    except (SanitizeError, ValueError) as exc:
        print(f"GATE FAIL: {exc}")
        print("(raw SVG saved above for inspection)")
        return 1

    flat_path = out / f"{stamp}.flat.svg"
    flat_path.write_text(flat, encoding="utf-8")
    print(f"flat SVG : {flat_path}  ({len(flat)} bytes)")
    print(
        f"GATE PASS: motif_id={motif.id}  color_slots={list(motif.color_slots)} "
        f"(cap {get_settings().recraft_max_color_slots})"
    )
    print(f"symbol   : {motif.symbol[:200]}{'...' if len(motif.symbol) > 200 else ''}")
    return 0


def cmd_passrate(args) -> int:
    paths = sorted(Path(args.samples_dir).glob("*.svg"))
    if not paths:
        print(f"no *.svg files in {args.samples_dir!r}", file=sys.stderr)
        return 2

    passed = 0
    for path in paths:
        raw = path.read_text(encoding="utf-8")
        try:
            _flat, motif = _gate(raw, args.max_slots)
        except (SanitizeError, ValueError) as exc:
            print(f"FAIL  {path.name}  {exc}")
            continue
        passed += 1
        print(f"PASS  {path.name}  slots={len(motif.color_slots)}")

    rate = passed / len(paths)
    print(f"\npass-rate: {passed}/{len(paths)} = {rate:.1%}  (max_slots={args.max_slots})")
    return 0


def cmd_tile(args) -> int:
    client = _client_or_exit()
    print(f"prompt   : {args.prompt}")
    print("1/3 generating motif via Recraft ...", flush=True)
    settings = get_settings()
    _flat, motif = _gate(
        client.generate(args.prompt),
        settings.recraft_max_color_slots,
        max_aspect_ratio=settings.motif_max_aspect_ratio,
        edge_seam_tol=settings.motif_edge_seam_tol,
        render_check=settings.motif_render_check,
    )
    motif_id = register_motif(motif)
    motif = get_motif(motif_id)
    print(f"    motif_id={motif_id}  color_slots={list(motif.color_slots)}")

    print("2/3 building seamless tile intent + composing ...", flush=True)
    intent = build_tile_intent(motif_id, seed=args.seed)
    candidate = generate(intent, seed=args.seed)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    stem = out / _slug(args.prompt)
    svg_path = stem.with_suffix(".tile.svg")
    svg_path.write_text(candidate.svg, encoding="utf-8")
    print(f"    tile SVG : {svg_path}  ({len(candidate.svg)} bytes)")

    print("3/3 rendering PNG preview (if a renderer is available) ...", flush=True)
    binary = find_renderer()
    if binary:
        try:
            png, _ = rasterize(
                candidate.svg,
                "png",
                dpi=150,
                width_mm=intent["canvas"]["tile_mm"],
                binary=binary,
            )
            png_path = stem.with_suffix(".tile.png")
            png_path.write_bytes(png)
            print(f"    tile PNG : {png_path}  ({len(png)} bytes)")
        except RasterError as exc:
            print(f"    PNG skipped: {exc}")
    else:
        print("    PNG skipped: no SVG renderer found (brew install librsvg or resvg)")
    print("done.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    smoke = sub.add_parser("smoke", help="call Recraft once and inspect the gate")
    smoke.add_argument("prompt", help="text prompt for the vector motif")
    smoke.add_argument("--out", default="recraft_out")
    smoke.add_argument("--model", default=None)
    smoke.add_argument("--b64", action="store_true")
    smoke.set_defaults(func=cmd_smoke)

    passrate = sub.add_parser("passrate", help="measure gate pass-rate over SVG samples")
    passrate.add_argument("samples_dir", help="folder of raw Recraft *.svg samples")
    passrate.add_argument(
        "--max-slots",
        type=int,
        default=get_settings().recraft_max_color_slots,
        help="color-slot cap (default: recraft_max_color_slots from config)",
    )
    passrate.set_defaults(func=cmd_passrate)

    tile = sub.add_parser("tile", help="generate a motif and wrap it in a tile")
    tile.add_argument("prompt", nargs="?", default=_DEFAULT_PROMPT)
    tile.add_argument("--out", default="recraft_out")
    tile.add_argument("--seed", type=int, default=7)
    tile.set_defaults(func=cmd_tile)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
