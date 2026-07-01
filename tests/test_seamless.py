import pytest

from app.validate.seamless import edge_seam


def _tile(width: int, height: int, pixel=(0, 0, 0, 0)):
    return [[pixel for _ in range(width)] for _ in range(height)]


def test_edge_seam_zero_when_opposite_edges_match():
    tile = _tile(8, 8)
    for row in tile:
        row[3:5] = [(200, 200, 200, 200)] * 2
    assert edge_seam(tile) == (0.0, 0.0)


def test_edge_seam_detects_mismatched_edges():
    tile = _tile(8, 8)
    for row in tile:
        row[-1] = (255, 255, 255, 255)
    seam_x, _ = edge_seam(tile)
    assert seam_x > 100


def test_seam_checks_reject_ragged_rows():
    with pytest.raises(ValueError, match="rectangular"):
        edge_seam([[(0, 0, 0, 0)], [(0, 0, 0, 0), (0, 0, 0, 0)]])
