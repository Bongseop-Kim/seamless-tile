"""List the named colorway palettes available for recoloring."""

from fastapi import APIRouter

from app.domain.colorway import PALETTES

router = APIRouter(prefix="/palettes", tags=["palettes"])


@router.get("")
def list_palettes() -> dict[str, list[str]]:
    return {name: list(colors) for name, colors in PALETTES.items()}
