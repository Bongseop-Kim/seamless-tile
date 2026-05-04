import numpy as np


def make_cross_mask(size: tuple[int, int], width: int) -> np.ndarray:
    h, w = size
    if width < 1:
        raise ValueError("mask width must be at least 1")

    mask = np.zeros((h, w), dtype=np.uint8)
    cx, cy = w // 2, h // 2
    half = width // 2
    extra = width % 2

    x0 = max(0, cx - half)
    x1 = min(w, cx + half + extra)
    y0 = max(0, cy - half)
    y1 = min(h, cy + half + extra)

    mask[:, x0:x1] = 255
    mask[y0:y1, :] = 255
    return mask

