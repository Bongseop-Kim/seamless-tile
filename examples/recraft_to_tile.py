"""End-to-end example: Recraft motif -> seamless tile (default: a pig riding a bicycle).

Pipeline:
  1. Recraft generates ONE vector object (suitability gate flattens it, strips the
     background, tight-bbox frames it) and registers it -> motif_id.
  2. ``build_tile_intent`` wraps that motif in an intent with a half-drop lattice +
     background, binding each motif color slot to a palette color.
  3. The deterministic engine composes a seamless tile SVG (and an optional PNG preview).

The engine does the placement/repetition, so the motif must be a single object — that is
exactly what the Recraft gate now guarantees. Needs RECRAFT_API_KEY in .env. The
``build_tile_intent`` helper is import-tested offline in tests/test_example_tile.py.

Usage:
    .venv/bin/python examples/recraft_to_tile.py
    .venv/bin/python examples/recraft_to_tile.py "a single robot waving" --out examples/output
"""

from __future__ import annotations

import argparse
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.adapters.recraft import client_from_settings, create_motif  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.engine.generate import generate  # noqa: E402
from app.motifs.registry import get_motif  # noqa: E402
from app.render.raster import RasterError, find_renderer, rasterize  # noqa: E402

# Colorway for the example. Each motif color slot (s0, s1, …) binds to one of these in
# document order; the rendered tile uses these, not Recraft's original colors.
_PALETTE = ["#ef9aa6", "#2e2a2a", "#fbf3e0", "#2f8f83", "#e8b23a", "#c8553d"]
_BG_HEX = "#f5efe3"
_DEFAULT_PROMPT = (
    "a single pig riding a bicycle, flat vector illustration, bold simple shapes, "
    "centered, no background"
)


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
    """Wrap a registered motif in a seamless half-drop lattice intent.

    Builds the palette + colorway from the motif's ``color_slots`` (each slot -> one
    palette color), so the same helper works for single- and multi-color motifs.
    """
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
        "production": {"method": "digital", "max_colors": 12},
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


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40] or "motif"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("prompt", nargs="?", default=_DEFAULT_PROMPT)
    parser.add_argument("--out", default="examples/output")
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()

    client = client_from_settings(get_settings())
    if client is None:
        print("RECRAFT_API_KEY is not set in .env — cannot call the API.", file=sys.stderr)
        return 2

    print(f"prompt   : {args.prompt}")
    print("1/3 generating motif via Recraft ...", flush=True)
    motif_id = create_motif(args.prompt, client=client)
    motif = get_motif(motif_id)
    print(f"    motif_id={motif_id}  color_slots={list(motif.color_slots)}")

    print("2/3 building seamless tile intent + composing ...", flush=True)
    intent = build_tile_intent(motif_id, seed=args.seed)
    candidate = generate(intent, seed=args.seed)

    os.makedirs(args.out, exist_ok=True)
    stem = os.path.join(args.out, _slug(args.prompt))
    svg_path = f"{stem}.tile.svg"
    with open(svg_path, "w", encoding="utf-8") as fh:
        fh.write(candidate.svg)
    print(f"    tile SVG : {svg_path}  ({len(candidate.svg)} bytes)")

    print("3/3 rendering PNG preview (if a renderer is available) ...", flush=True)
    if find_renderer():
        try:
            png, _ = rasterize(candidate.svg, "png", dpi=150, width_mm=intent["canvas"]["tile_mm"])
            png_path = f"{stem}.tile.png"
            with open(png_path, "wb") as fh:
                fh.write(png)
            print(f"    tile PNG : {png_path}  ({len(png)} bytes)")
        except RasterError as exc:
            print(f"    PNG skipped: {exc}")
    else:
        print("    PNG skipped: no SVG renderer found (brew install librsvg or resvg)")
    print("done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
