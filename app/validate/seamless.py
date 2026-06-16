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
