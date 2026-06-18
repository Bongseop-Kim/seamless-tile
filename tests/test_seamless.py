import numpy as np

from app.validate.seamless import edge_seam, seamless_diff


def test_seamless_diff_zero_on_uniform():
    tile = np.zeros((16, 16, 4), dtype=np.uint8)
    assert seamless_diff(tile) == (0.0, 0.0)


def test_seamless_diff_detects_horizontal_discontinuity():
    tile = np.zeros((16, 16, 4), dtype=np.uint8)
    tile[:, 8:] = 255
    seam_x, _ = seamless_diff(tile)
    assert seam_x > 100


def test_seamless_diff_detects_vertical_discontinuity():
    tile = np.zeros((16, 16, 4), dtype=np.uint8)
    tile[8:, :] = 255
    _, seam_y = seamless_diff(tile)
    assert seam_y > 100


def test_edge_seam_zero_when_opposite_edges_match():
    tile = np.zeros((8, 8, 4), dtype=np.uint8)
    tile[:, 3:5] = 200
    assert edge_seam(tile) == (0.0, 0.0)


def test_edge_seam_detects_mismatched_edges():
    tile = np.zeros((8, 8, 4), dtype=np.uint8)
    tile[:, -1] = 255
    seam_x, _ = edge_seam(tile)
    assert seam_x > 100
