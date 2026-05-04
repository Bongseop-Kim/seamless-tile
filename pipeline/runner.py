import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from pipeline.inpaint import InpaintModel, inpaint
from pipeline.mask import make_cross_mask
from pipeline.offset import inverse_offset, offset
from pipeline.repeat import repeat_grid


@dataclass(frozen=True)
class PipelineResult:
    output_dir: Path
    stages: dict[str, Path]


async def run_pipeline(
    image: Image.Image,
    output_dir: Path,
    mask_width: int,
    model: InpaintModel,
    prompt: str,
    strength: float = 0.7,
) -> PipelineResult:
    output_dir.mkdir(parents=True, exist_ok=True)

    original = image.convert("RGB")
    arr = np.asarray(original)
    h, w = arr.shape[:2]
    dx, dy = w // 2, h // 2

    offset_img = Image.fromarray(offset(arr, dx=dx, dy=dy))
    mask_arr = make_cross_mask((h, w), mask_width)
    mask_img = Image.fromarray(mask_arr, mode="L")
    overlay = _mask_overlay(offset_img, mask_img)
    inpainted = await inpaint(offset_img, mask_img, model=model, prompt=prompt, strength=strength)
    final = Image.fromarray(inverse_offset(np.asarray(inpainted.convert("RGB")), dx=dx, dy=dy))
    preview_2x2 = repeat_grid(final, n=2)
    preview_3x3 = repeat_grid(final, n=3)

    stages = {
        "original": _save(output_dir, "original", original),
        "offset": _save(output_dir, "offset", offset_img),
        "mask": _save(output_dir, "mask", mask_img),
        "mask_overlay": _save(output_dir, "mask_overlay", overlay),
        "inpainted": _save(output_dir, "inpainted", inpainted),
        "final": _save(output_dir, "final", final),
        "2x2": _save(output_dir, "2x2", preview_2x2),
        "3x3": _save(output_dir, "3x3", preview_3x3),
    }
    _write_params(output_dir, mask_width, model, prompt, strength, original.size)
    return PipelineResult(output_dir=output_dir, stages=stages)


def _save(output_dir: Path, name: str, image: Image.Image) -> Path:
    path = output_dir / f"{name}.png"
    image.save(path)
    return path


def _mask_overlay(image: Image.Image, mask: Image.Image) -> Image.Image:
    base = image.convert("RGBA")
    red = Image.new("RGBA", base.size, (255, 56, 56, 120))
    transparent = Image.new("RGBA", base.size, (0, 0, 0, 0))
    layer = Image.composite(red, transparent, mask.convert("L"))
    return Image.alpha_composite(base, layer).convert("RGB")


def _write_params(
    output_dir: Path,
    mask_width: int,
    model: InpaintModel,
    prompt: str,
    strength: float,
    size: tuple[int, int],
) -> None:
    params: dict[str, Any] = {
        "mask_width": mask_width,
        "model": model,
        "prompt": prompt,
        "strength": strength,
        "size": list(size),
    }
    (output_dir / "params.json").write_text(
        json.dumps(params, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

