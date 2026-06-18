"""Lattice placement: instances on a two-basis-vector grid.

Absorbs the old ``repeat.py`` block/half_drop/brick modes and generalizes "drop"
to ``drop_fraction`` (1/2, 1/3, 1/4):

- block:     b1 = (cell_w, 0),           b2 = (0, cell_h)
- half_drop: b1 = (cell_w, cell_h*drop), b2 = (0, cell_h)           (drop_axis="column")
- brick:     b1 = (cell_w, 0),           b2 = (cell_w*drop, cell_h) (drop_axis="row")

Every lattice point inside ``[0, tile)^2`` is enumerated and wrapped onto the torus
(``% tile``). Seamlessness follows by construction when the cell divides the tile and
the drop closes on the torus lattice (both enforced upstream by ``validate_intent``).
For ``drop_axis="column"`` the x-coordinate depends only on the column index and the
y-coordinate only on (column, row), so the ``nx*ny`` enumerated points are distinct;
``drop_axis="row"`` is the transpose.
"""

from __future__ import annotations

from app.engine.intent import Placement
from app.engine.placement.path_following import Instance


def place_lattice(placement: Placement, tile_mm: float) -> list[Instance]:
    spec = placement.lattice
    if spec is None:
        raise ValueError("lattice placement requires a `lattice` spec")

    cw, ch = spec.cell_w_mm, spec.cell_h_mm
    drop = spec.drop_fraction or 0.0
    nx = round(tile_mm / cw)
    ny = round(tile_mm / ch)

    if spec.drop_axis == "column":
        b1 = (cw, ch * drop)
        b2 = (0.0, ch)
    else:  # row
        b1 = (cw, 0.0)
        b2 = (cw * drop, ch)

    instances: list[Instance] = []
    for i in range(nx):
        for j in range(ny):
            x = i * b1[0] + j * b2[0]
            y = i * b1[1] + j * b2[1]
            instances.append(Instance(x % tile_mm, y % tile_mm, 0.0))
    return instances
