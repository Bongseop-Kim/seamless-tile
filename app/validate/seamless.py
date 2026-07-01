"""Seamless boundary check: roll a rasterised tile by half and measure the
discontinuity at the wrap edge. 0 means a perfect, invisible seam."""

from collections.abc import Sequence


Pixel = Sequence[int]
Rows = list[list[Pixel]]


def _rows(tile_rgba) -> Rows:
    """Normalize a PIL image or nested RGBA pixel rows to list-backed rows."""
    if hasattr(tile_rgba, "getdata") and hasattr(tile_rgba, "size"):
        width, height = tile_rgba.size
        data_source = getattr(tile_rgba, "get_flattened_data", tile_rgba.getdata)
        data = list(data_source())
        return [data[y * width : (y + 1) * width] for y in range(height)]
    rows = []
    width = None
    try:
        for row in tile_rgba:
            normalized = [tuple(int(c) for c in pixel) for pixel in row]
            if width is None:
                width = len(normalized)
            elif len(normalized) != width:
                raise ValueError("tile_rgba rows must be rectangular")
            rows.append(normalized)
    except TypeError as exc:
        raise ValueError("tile_rgba must be a rectangular 2D array") from exc
    return rows


def _mean_abs(pairs) -> float:
    total = 0
    count = 0
    for a, b in pairs:
        for ca, cb in zip(a, b):
            total += abs(int(ca) - int(cb))
            count += 1
    return float(total / count) if count else 0.0


def edge_seam(tile_rgba) -> tuple[float, float]:
    """Mean per-channel difference between opposite edges of one tile.

    When the tile repeats, column -1 abuts the next tile's column 0 (and row -1
    abuts row 0). Small values mean the seam is invisible. Hard-edged flat
    patterns can legitimately differ here, so verify those by construction
    instead.
    """
    arr = _rows(tile_rgba)
    seam_x = _mean_abs((row[0], row[-1]) for row in arr)
    seam_y = _mean_abs(zip(arr[0], arr[-1]))
    return seam_x, seam_y


# Tolerance for ``tiling_seam`` excess (per-channel mean). A seamless tile's seam
# discontinuity should not exceed its own interior baseline by more than this.
TILING_SEAM_TOL = 1.0


def tiling_seam(tiled_rgba, tile_px: int, margin: int = 4) -> tuple[float, float]:
    """Excess discontinuity at an internal tile seam over the interior baseline.

    Given an N-tile raster (the ``<pattern>`` rendered across multiple tiles), this
    compares the adjacent-pixel difference at the internal seam (column/row
    ``tile_px``) against the worst interior adjacent-pixel difference, and returns
    ``(excess_x, excess_y)``. A value ``<= 0`` means the tile repeats with no seam
    beyond what its own interior edges already produce.

    Unlike ``edge_seam`` (which compares a single tile's outermost rows/cols), this
    is robust to hard edges that merely land on the tile boundary: the renderer
    anti-aliases the seam exactly as it does interior edges, and the interior
    baseline absorbs that. By-construction invariants remain the primary guarantee;
    this is the raster regression guard.

    Caveat: this catches only seams *larger* than the worst interior edge. A real
    seam whose magnitude is <= the interior baseline is masked — which is why the
    by-construction invariants, not this metric, are the load-bearing guarantee.
    """
    arr = _rows(tiled_rgba)
    if not arr or not arr[0]:
        raise ValueError("tiled_rgba must be at least a 2D array")
    h, w = len(arr), len(arr[0])
    if margin < 0:
        raise ValueError("margin must be non-negative")
    if tile_px <= 0:
        raise ValueError("tile_px must be greater than 0")
    if tile_px < margin:
        raise ValueError("tile_px must be greater than or equal to margin")
    if tile_px >= w - margin or tile_px >= h - margin:
        raise ValueError(
            "tile_px must be less than both width - margin and height - margin"
        )

    def col_disc(c: int) -> float:
        return _mean_abs((row[c], row[c - 1]) for row in arr)

    def row_disc(r: int) -> float:
        return _mean_abs(zip(arr[r], arr[r - 1]))

    seam_x = col_disc(tile_px)
    seam_y = row_disc(tile_px)
    base_x = max(
        (col_disc(c) for c in range(margin, w - margin) if abs(c - tile_px) > margin),
        default=0.0,
    )
    base_y = max(
        (row_disc(r) for r in range(margin, h - margin) if abs(r - tile_px) > margin),
        default=0.0,
    )
    return seam_x - base_x, seam_y - base_y
