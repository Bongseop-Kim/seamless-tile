import numpy as np


def offset(img: np.ndarray, dx: int, dy: int) -> np.ndarray:
    return np.roll(img, shift=(dy, dx), axis=(0, 1))


def inverse_offset(img: np.ndarray, dx: int, dy: int) -> np.ndarray:
    return np.roll(img, shift=(-dy, -dx), axis=(0, 1))

