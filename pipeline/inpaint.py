import os
from io import BytesIO
from typing import Literal

import httpx
from PIL import Image, ImageFilter

InpaintModel = Literal["local-preview", "gpt-image-2", "fal-sdxl"]


async def inpaint(
    img: Image.Image,
    mask: Image.Image,
    model: InpaintModel,
    prompt: str,
    strength: float = 0.7,
) -> Image.Image:
    if model == "local-preview":
        return _local_preview_inpaint(img, mask, strength)
    if model == "gpt-image-2":
        return await _gpt_image_inpaint(img, mask, prompt)
    if model == "fal-sdxl":
        return await _fal_inpaint(img, mask, prompt, strength)
    raise ValueError(f"unsupported inpaint model: {model}")


def _local_preview_inpaint(img: Image.Image, mask: Image.Image, strength: float) -> Image.Image:
    base = img.convert("RGB")
    mask_l = mask.convert("L")
    radius = max(1, int(8 * max(0.0, min(1.0, strength))))
    blurred = base.filter(ImageFilter.GaussianBlur(radius=radius))
    return Image.composite(blurred, base, mask_l)


async def _gpt_image_inpaint(img: Image.Image, mask: Image.Image, prompt: str) -> Image.Image:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is required for gpt-image-2")

    image_bytes = BytesIO()
    mask_bytes = BytesIO()
    img.convert("RGBA").save(image_bytes, format="PNG")
    mask.convert("L").save(mask_bytes, format="PNG")
    image_bytes.seek(0)
    mask_bytes.seek(0)

    headers = {"Authorization": f"Bearer {api_key}"}
    files = {
        "image": ("image.png", image_bytes, "image/png"),
        "mask": ("mask.png", mask_bytes, "image/png"),
    }
    data = {"model": "gpt-image-2", "prompt": prompt}
    async with httpx.AsyncClient(timeout=120) as client:
        response = await client.post(
            "https://api.openai.com/v1/images/edits",
            headers=headers,
            data=data,
            files=files,
        )
        response.raise_for_status()
        payload = response.json()

    b64_json = payload["data"][0].get("b64_json")
    if not b64_json:
        raise RuntimeError("OpenAI image edit response did not include b64_json")

    import base64

    return Image.open(BytesIO(base64.b64decode(b64_json))).convert("RGB")


async def _fal_inpaint(
    img: Image.Image,
    mask: Image.Image,
    prompt: str,
    strength: float,
) -> Image.Image:
    _ = (img, mask, prompt, strength)
    if not os.getenv("FAL_KEY"):
        raise RuntimeError("FAL_KEY is required for fal-sdxl")
    raise NotImplementedError("fal-sdxl client is not implemented yet")

