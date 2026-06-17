"""Repeat lattice utilities for seamless placement."""

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
