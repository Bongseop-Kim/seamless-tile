from app.validate.seamless import edge_seam, seamless_diff


def _tile(width: int, height: int, pixel=(0, 0, 0, 0)):
    return [[pixel for _ in range(width)] for _ in range(height)]


def test_seamless_diff_zero_on_uniform():
    tile = _tile(16, 16)
    assert seamless_diff(tile) == (0.0, 0.0)


def test_seamless_diff_detects_horizontal_discontinuity():
    tile = _tile(16, 16)
    for row in tile:
        row[8:] = [(255, 255, 255, 255)] * 8
    seam_x, _ = seamless_diff(tile)
    assert seam_x > 100


def test_seamless_diff_detects_vertical_discontinuity():
    tile = _tile(16, 16)
    for y in range(8, 16):
        tile[y] = [(255, 255, 255, 255)] * 16
    _, seam_y = seamless_diff(tile)
    assert seam_y > 100


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
