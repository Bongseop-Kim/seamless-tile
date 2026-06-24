"""Candidate preview PNGs: render an SVG to PNG once and upload to Supabase Storage.

The generate route renders each candidate's SVG to a PNG at generation time and
uploads it here; the API response carries only the resulting public URL, so clients
never trigger a re-render. Configured iff ``supabase_url`` + ``supabase_service_key``
are set; otherwise :func:`preview_configured` is False and the route degrades to a
null ``png_url`` (mirrors the motif store's no-DSN no-op).

Upload uses the Supabase Storage REST API over httpx (already a dependency) — no
supabase client SDK. The service key is server-side only (RLS bypass).
"""

from __future__ import annotations

import httpx

from app.core.config import get_settings
from app.render.raster import rasterize

_TIMEOUT = 15.0


def preview_configured() -> bool:
    s = get_settings()
    return bool(s.supabase_url and s.supabase_service_key)


def make_preview(svg: str, *, tile_mm: float, dpi: int, path: str) -> str:
    """Render ``svg`` to a PNG (one tile at ``dpi``) and upload it to the preview
    bucket at ``path``; return the object's public URL.

    Raises ``RasterError`` if rendering fails (e.g. no SVG renderer installed) or
    ``httpx.HTTPError`` if the upload fails. Callers treat either as a per-candidate
    best-effort miss. Assumes :func:`preview_configured` is True.
    """
    png, _media = rasterize(svg, "png", dpi=dpi, width_mm=tile_mm)
    s = get_settings()
    base = s.supabase_url.rstrip("/")
    bucket = s.preview_bucket
    resp = httpx.post(
        f"{base}/storage/v1/object/{bucket}/{path}",
        content=png,
        headers={
            "Authorization": f"Bearer {s.supabase_service_key}",
            "apikey": s.supabase_service_key,
            "Content-Type": "image/png",
            "x-upsert": "true",  # idempotent: re-running a deterministic request overwrites
        },
        timeout=_TIMEOUT,
    )
    if resp.is_error:
        raise httpx.HTTPStatusError(
            f"{resp.status_code} {resp.reason_phrase}: {resp.text[:500]}",
            request=resp.request,
            response=resp,
        )
    return f"{base}/storage/v1/object/public/{bucket}/{path}"
