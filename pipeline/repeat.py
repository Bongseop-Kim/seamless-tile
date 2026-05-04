from PIL import Image


def repeat_grid(tile: Image.Image, n: int) -> Image.Image:
    if n < 1:
        raise ValueError("repeat count must be at least 1")

    w, h = tile.size
    out = Image.new(tile.mode, (w * n, h * n))
    for row in range(n):
        for col in range(n):
            out.paste(tile, (col * w, row * h))
    return out

