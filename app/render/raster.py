"""Rasterize SVG to PNG/TIFF via an external renderer (resvg, or rsvg-convert
as fallback) then re-encode with Pillow to stamp physical DPI.

A CLI subprocess is used deliberately: it renders SVG filters (feTurbulence,
feDisplacementMap) faithfully and is immune to Python-version wheel breakage.
"""

import io
import os
import shutil
import subprocess

from PIL import Image

from app.domain.units import mm_to_px

MAX_DIMENSION_PX = 20000  # guard against accidental multi-GB rasters

_MEDIA = {"png": "image/png", "tiff": "image/tiff"}


class RasterError(RuntimeError):
    pass


def find_renderer(preferred: str | None = None) -> str | None:
    if preferred:
        return shutil.which(preferred) or (preferred if os.path.exists(preferred) else None)
    # Prefer rsvg-convert: it honours feTurbulence stitchTiles, so textured
    # tiles stay seamless. resvg is the fallback (no stitch support).
    return shutil.which("rsvg-convert") or shutil.which("resvg")


def _render_png_bytes(svg: str, width_px: int, height_px: int, binary: str) -> bytes:
    name = os.path.basename(binary)
    if "resvg" in name:
        cmd = [binary, "-w", str(width_px), "-h", str(height_px), "-", "-c"]
    else:  # rsvg-convert: read SVG from stdin (-), write PNG to stdout
        cmd = [binary, "-w", str(width_px), "-h", str(height_px), "-f", "png", "-"]
    proc = subprocess.run(cmd, input=svg.encode("utf-8"), capture_output=True)
    if proc.returncode != 0 or not proc.stdout:
        msg = (proc.stderr or b"").decode("utf-8", "replace").strip()
        raise RasterError(msg or f"{name} returned no output")
    return proc.stdout


def rasterize(
    svg: str,
    fmt: str,
    dpi: int,
    width_mm: float,
    height_mm: float | None = None,
    binary: str | None = None,
) -> tuple[bytes, str]:
    if fmt not in _MEDIA:
        raise RasterError(f"unsupported raster format: {fmt}")
    binary = binary or find_renderer()
    if not binary:
        raise RasterError("no SVG renderer found; install librsvg (brew install librsvg)")

    height_mm = width_mm if height_mm is None else height_mm
    width_px = max(1, mm_to_px(width_mm, dpi))
    height_px = max(1, mm_to_px(height_mm, dpi))
    if width_px > MAX_DIMENSION_PX or height_px > MAX_DIMENSION_PX:
        raise RasterError(
            f"raster too large ({width_px}x{height_px}px); reduce dpi or width_mm"
        )

    png = _render_png_bytes(svg, width_px, height_px, binary)
    image = Image.open(io.BytesIO(png))
    out = io.BytesIO()
    if fmt == "png":
        image.save(out, format="PNG", dpi=(dpi, dpi))
    else:
        image.save(out, format="TIFF", dpi=(dpi, dpi), compression="tiff_lzw")
    return out.getvalue(), _MEDIA[fmt]
