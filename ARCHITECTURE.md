# Architecture — Seamless Textile Pattern Generator

As-built design of the FastAPI service that generates seamless textile patterns
as SVG and rasterizes them to PNG/TIFF.

## Principles

- **Vector-first**: SVG is the single source of truth; PNG/TIFF are derived by
  rasterizing the SVG. Lossless across print resolutions.
- **mm-based units**: geometry is millimetres internally; `px = round(mm / 25.4 × dpi)`
  only at the raster boundary. DPI is embedded in the output file.
- **Minimal dependencies**: SVG is built with the stdlib (f-strings / `xml.etree`),
  no SVG-generation library. Rasterization is delegated to a CLI binary.
- **Generation ≠ repeat**: motif generation is separate from repeat layout
  (block / half_drop / brick).
- **Seamless by construction**: SVG `<pattern patternUnits="userSpaceOnUse">` tiles
  by translation; a tile is made seamless via the repeat lattice, not by pixel fixups.

## Layout

Everything lives under the `app/` package (single import root; no editable install).

```
app/
├── main.py                 # create_app(): wires routers under /api/v1
├── core/config.py          # pydantic-settings: renderer_bin, default_dpi, max_dpi, max_tile_mm
├── domain/                 # framework-independent core
│   ├── units.py            # mm<->px, SVG number formatting
│   ├── colorway.py         # Colorway (hex, cyclic) + named PALETTES
│   ├── repeat.py           # RepeatMode + placements() (2x-tile trick)
│   ├── tile.py             # <pattern> assembly
│   └── pattern.py          # Pattern ABC: motif() + to_pattern_def()
├── patterns/               # Stripe / Check (gingham) / Dot / Herringbone
├── render/
│   ├── svg.py              # standalone mm-unit SVG document
│   └── raster.py           # rsvg-convert|resvg subprocess -> Pillow (PNG/TIFF + DPI)
├── validate/seamless.py    # seam metrics (seamless_diff, edge_seam)
└── api/
    ├── routes/             # health, patterns, export, palettes
    ├── schemas/            # per-pattern request validation + colorway
    └── deps.py             # in-memory pattern store (id -> Pattern)
```

## Request flow

1. `POST /api/v1/patterns/{type}` validates a typed Pydantic body, builds a
   `Pattern` (geometry + `Colorway`), stores it as `id -> Pattern`, and returns
   `{id, svg}`.
2. `GET /patterns/{id}` re-renders the stored `Pattern` to SVG on demand.
3. `GET /patterns/{id}/export` negotiates `format` (svg|png|tiff), `dpi`, `width_mm`.
   Raster goes through `render/raster.py`.
4. `POST /patterns/{id}/colorway` shallow-copies the stored `Pattern`, swaps only
   its `Colorway` (geometry untouched), and stores it under a new id.

Storing the `Pattern` object (not a frozen SVG string) is what makes on-demand
resizing and zero-geometry recoloring cheap.

## Seamless repeat (`domain/repeat.py`)

A motif occupies a `W×H` cell. `placements()` returns the compound tile size and
the offsets at which to stamp the motif so the compound tile is a pure
translational repeat:

- **block**: `(W, H)`, stamp `(0,0)`.
- **half_drop**: `(2W, H)`, stamps `(0,0)`, `(W, H/2)`, `(W, -H/2)` — the half-drop
  lattice's rectangular period plus the vertical wrap copy.
- **brick**: `(W, 2H)`, stamps `(0,0)`, `(W/2, H)`, `(-W/2, H)`.

Dots rely on this: a centred dot stays inside its cell, and under half_drop the
wrap stamps reconstruct any dot that lands on a tile edge — no per-dot corner math.

## Rasterization (`render/raster.py`)

SVG → PNG via a CLI subprocess (SVG fed on stdin), then Pillow re-encodes to embed
physical DPI (PNG `pHYs`; TIFF `XResolution`/`YResolution` + LZW). The pixel size is
computed explicitly from `width_mm × dpi`, so embedded DPI is consistent metadata.

Renderer preference: **rsvg-convert (librsvg) → resvg**. Guards: `dpi ≤ max_dpi`,
`width_mm ≤ max_tile_mm`, and a hard pixel-dimension cap.

## Known limitations

- In-memory store: ids are lost on restart and not shared across worker processes.
