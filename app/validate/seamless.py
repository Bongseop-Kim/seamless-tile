"""Seamless boundary check: roll a rasterised tile by half and measure the
discontinuity at the wrap edge. 0 means a perfect, invisible seam."""

import numpy as np


def seamless_diff(tile_rgba: np.ndarray) -> tuple[float, float]:
    """Return (seam_x, seam_y): mean absolute edge discontinuity per channel."""
    arr = np.asarray(tile_rgba).astype(np.int16)
    h, w = arr.shape[:2]
    rolled_x = np.roll(arr, w // 2, axis=1)
    rolled_y = np.roll(arr, h // 2, axis=0)
    seam_x = float(np.abs(arr[:, 0] - rolled_x[:, 0]).mean())
    seam_y = float(np.abs(arr[0, :] - rolled_y[0, :]).mean())
    return seam_x, seam_y
