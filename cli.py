import argparse
import asyncio
from pathlib import Path

from dotenv import load_dotenv
from PIL import Image

from pipeline.runner import run_pipeline


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the seamless tile pipeline locally.")
    parser.add_argument("image", type=Path)
    parser.add_argument("--output", type=Path, default=Path("runs/cli"))
    parser.add_argument("--mask-width", type=int, default=60)
    parser.add_argument("--model", choices=["local-preview", "gpt-image-2", "fal-sdxl"], default="local-preview")
    parser.add_argument("--prompt", default="preserve the texture and pattern phase across the seam")
    parser.add_argument("--strength", type=float, default=0.7)
    return parser.parse_args()


async def main() -> None:
    load_dotenv()
    args = parse_args()
    image = Image.open(args.image)
    result = await run_pipeline(
        image=image,
        output_dir=args.output,
        mask_width=args.mask_width,
        model=args.model,
        prompt=args.prompt,
        strength=args.strength,
    )
    for name, path in result.stages.items():
        print(f"{name}: {path}")


if __name__ == "__main__":
    asyncio.run(main())
