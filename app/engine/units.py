"""Unit conversion and SVG number formatting for engine geometry."""

import math
from dataclasses import dataclass
from fractions import Fraction

DEFAULT_DPI = 300
MM_PER_INCH = 25.4


def mm_to_px(mm: float, dpi: int = DEFAULT_DPI) -> int:
    return round(mm / MM_PER_INCH * dpi)


def fmt(value: float) -> str:
    s = f"{float(value):.4f}".rstrip("0").rstrip(".")
    if s in ("", "-0", "-"):
        return "0"
    return s


# Maximum number of tiles a snapped diagonal lane crosses before it closes onto
# itself (the denominator cap for tan(theta) = p/q). Single-source commensurability
# knob referenced by sessions 1/2/4: larger -> closer angle snap but a longer
# <pattern> repeat; smaller -> a coarser snap. See docs/plan/00-overview.md.
MAX_LANE_PERIOD_TILES = 16


@dataclass(frozen=True)
class SnappedAngle:
    """A lane angle snapped to a tile-commensurate rational slope ``p/q``.

    ``deviation_deg`` is ``angle_deg - requested_deg`` (snapped minus request),
    surfaced by validation. ``q == 0`` denotes a vertical lane.
    """

    angle_deg: float
    p: int
    q: int
    deviation_deg: float


def _deviation(snapped_deg: float, requested_deg: float) -> float:
    # Lane directions are mod 180 deg; report the signed snap error reduced into (-90, 90].
    return ((snapped_deg - requested_deg + 90.0) % 180.0) - 90.0


def snap_angle(requested_deg: float) -> SnappedAngle:
    """Snap a requested lane angle to the nearest tile-commensurate direction.

    On a square tile a straight lane is seamless only when its slope ``tan(theta)``
    is rational ``p/q``; the lane then closes onto itself after ``q`` tiles in x and
    ``p`` tiles in y. This returns the nearest such direction via best rational
    approximation of the slope (bounded by ``MAX_LANE_PERIOD_TILES``), preserving
    sign, together with the deviation from the request.

    For a square tile the snapped slope depends only on ``requested_deg`` and the
    denominator cap. Band-phase (period) seamlessness is a separate rule, enforced
    in session 4 -- not here.
    """
    # Slope-based snapping is well-defined mod 180 deg; normalize into (-90, 90].
    theta = ((requested_deg + 90.0) % 180.0) - 90.0
    cos_t = math.cos(math.radians(theta))
    if abs(abs(theta) - 90.0) < 1e-9 or abs(cos_t) < 1e-12:
        return SnappedAngle(90.0, 1, 0, _deviation(90.0, requested_deg))

    slope = math.tan(math.radians(theta))
    abs_slope = abs(slope)
    sign = -1 if slope < 0 else 1
    if abs_slope <= 1.0:
        frac = Fraction(abs_slope).limit_denominator(MAX_LANE_PERIOD_TILES)
        p_abs, q = frac.numerator, frac.denominator
    else:
        # Approximate the cotangent (in [0, 1)) for better-conditioned near-vertical
        # snapping, then invert: slope = 1 / cot.
        cot = Fraction(1.0 / abs_slope).limit_denominator(MAX_LANE_PERIOD_TILES)
        if cot.numerator == 0:
            return SnappedAngle(90.0, 1, 0, _deviation(90.0, requested_deg))
        p_abs, q = cot.denominator, cot.numerator

    p = sign * p_abs
    angle = math.degrees(math.atan2(p, q))
    return SnappedAngle(angle, p, q, _deviation(angle, requested_deg))


def stripe_tiles(tile_mm: float, period_mm: float, p: int, q: int, tol: float = 1e-6) -> bool:
    """True if a parallel-band stripe tiles a square ``tile_mm`` torus seamlessly.

    A family of parallel bands at perpendicular spacing ``period_mm`` and snapped
    slope ``(p, q)`` is invariant under the tile translations iff
    ``tile_mm == k * period_mm * hypot(p, q)`` for a positive integer ``k``. For an
    axis-aligned stripe (``hypot(p, q) == 1``) this reduces to ``period_mm | tile_mm``;
    a diagonal therefore tiles only at the discrete periods ``tile_mm / (k*hypot(p, q))``
    (so a non-Pythagorean slope, whose ``hypot`` is irrational, never tiles).
    """
    if period_mm <= 0:
        return False
    hypot = math.hypot(p, q)
    if hypot == 0:
        return False
    k = tile_mm / (period_mm * hypot)
    nearest = round(k)
    return nearest >= 1 and abs(nearest - k) <= tol * max(1.0, k)


def divides(whole: float, part: float, tol: float = 1e-6) -> bool:
    """True if ``part`` divides ``whole`` into integer multiples (within tolerance)."""
    if part <= 0:
        return False
    residue = round(whole / part) * part - whole
    return abs(residue) <= tol * max(1.0, abs(whole))


def snap_spacing(closure_mm: float, spacing_mm: float) -> tuple[int, float]:
    """Snap an along-lane spacing to an exact divisor of the closure length.

    Returns ``(n, spacing_eff)`` where ``n = max(1, round(closure/spacing))`` is the
    instance count over one torus period and ``spacing_eff = closure_mm / n`` divides
    the closure exactly, so the motif rhythm is uniform across the wrap. A diagonal lane
    has an irrational closure for most slopes, so the requested spacing rarely divides
    it; snapping (rather than rejecting) keeps the lane usable, mirroring ``snap_angle``.

    For a spacing that already divides the closure (e.g. the MVP tie), ``spacing_eff ==
    spacing_mm`` to within float precision -- output is unchanged (determinism preserved).
    """
    if spacing_mm <= 0:
        raise ValueError(f"spacing_mm must be positive, got {spacing_mm}")
    n = max(1, round(closure_mm / spacing_mm))
    return n, closure_mm / n
