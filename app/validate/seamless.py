"""Seamless boundary check: roll a rasterised tile by half and measure the
discontinuity at the wrap edge. 0 means a perfect, invisible seam."""

import numpy as np


def seamless_diff(tile_rgba: np.ndarray) -> tuple[float, float]:
    """Documented offset-inspect heuristic: roll the tile by half and compare.

    Kept as specified in the architecture doc. For a strict tileability check
    (does the left edge actually meet the right edge) use ``edge_seam``.
    """
    arr = np.asarray(tile_rgba).astype(np.int16)
    h, w = arr.shape[:2]
    rolled_x = np.roll(arr, w // 2, axis=1)
    rolled_y = np.roll(arr, h // 2, axis=0)
    seam_x = float(np.abs(arr[:, 0] - rolled_x[:, 0]).mean())
    seam_y = float(np.abs(arr[0, :] - rolled_y[0, :]).mean())
    return seam_x, seam_y


def edge_seam(tile_rgba: np.ndarray) -> tuple[float, float]:
    """Mean per-channel difference between opposite edges of one tile.

    When the tile repeats, column -1 abuts the next tile's column 0 (and row -1
    abuts row 0). Small values mean the seam is invisible. Hard-edged flat
    patterns can legitimately differ here, so verify those by construction
    instead.
    """
    arr = np.asarray(tile_rgba).astype(np.int16)
    seam_x = float(np.abs(arr[:, 0] - arr[:, -1]).mean())
    seam_y = float(np.abs(arr[0, :] - arr[-1, :]).mean())
    return seam_x, seam_y


# Tolerance for ``tiling_seam`` excess (per-channel mean). A seamless tile's seam
# discontinuity should not exceed its own interior baseline by more than this.
TILING_SEAM_TOL = 1.0


def tiling_seam(
    tiled_rgba: np.ndarray, tile_px: int, margin: int = 4
) -> tuple[float, float]:
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
    arr = np.asarray(tiled_rgba).astype(np.int16)
    if arr.ndim < 2:
        raise ValueError("tiled_rgba must be at least a 2D array")
    h, w = arr.shape[:2]
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
        return float(np.abs(arr[:, c] - arr[:, c - 1]).mean())

    def row_disc(r: int) -> float:
        return float(np.abs(arr[r, :] - arr[r - 1, :]).mean())

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
