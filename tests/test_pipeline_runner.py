import tempfile
import unittest
from pathlib import Path

from PIL import Image

from pipeline.runner import run_pipeline


class PipelineRunnerTests(unittest.IsolatedAsyncioTestCase):
    async def test_run_pipeline_writes_expected_stage_images(self):
        source = Image.new("RGB", (16, 16), "white")
        for x in range(16):
            source.putpixel((x, 8), (0, 0, 0))

        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp)

            result = await run_pipeline(
                image=source,
                output_dir=output_dir,
                mask_width=4,
                model="local-preview",
                prompt="preserve the simple stripe",
                strength=0.7,
            )

            expected = {
                "original",
                "offset",
                "mask",
                "mask_overlay",
                "inpainted",
                "final",
                "2x2",
                "3x3",
            }
            self.assertEqual(set(result.stages), expected)
            for stage_name, path in result.stages.items():
                self.assertTrue(path.exists(), stage_name)
                self.assertEqual(path.suffix, ".png")

            self.assertTrue((output_dir / "params.json").exists())
            with Image.open(result.stages["2x2"]) as preview_2x2:
                self.assertEqual(preview_2x2.size, (32, 32))
            with Image.open(result.stages["3x3"]) as preview_3x3:
                self.assertEqual(preview_3x3.size, (48, 48))


if __name__ == "__main__":
    unittest.main()
