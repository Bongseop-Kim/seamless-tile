"""Repeat layout: arrange a seamless base cell into a translational tile.

block      : 1x1 grid, tile == cell.
half_drop  : odd columns shifted down by H/2. The half-drop lattice has a
             rectangular period of (2W, H) containing two motif positions
             (0,0) and (W, H/2); the wrap copy (W, -H/2) completes the column.
brick      : odd rows shifted right by W/2. Symmetric: period (W, 2H) with
             stamps (0,0), (W/2, H) and the wrap copy (-W/2, H).

Each function returns (tile_w, tile_h, [(dx, dy), ...]) so the resulting
compound tile is a pure translational repeat and stays seamless.
"""

from enum import Enum


class RepeatMode(str, Enum):
    block = "block"
    half_drop = "half_drop"
    brick = "brick"


def placements(cell_w: float, cell_h: float, mode: RepeatMode):
    if mode == RepeatMode.block:
        return cell_w, cell_h, [(0.0, 0.0)]
    if mode == RepeatMode.half_drop:
        return (
            2 * cell_w,
            cell_h,
            [(0.0, 0.0), (cell_w, cell_h / 2), (cell_w, -cell_h / 2)],
        )
    if mode == RepeatMode.brick:
        return (
            cell_w,
            2 * cell_h,
            [(0.0, 0.0), (cell_w / 2, cell_h), (-cell_w / 2, cell_h)],
        )
    raise ValueError(f"unknown repeat mode: {mode}")
