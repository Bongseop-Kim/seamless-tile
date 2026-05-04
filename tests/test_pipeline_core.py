import unittest

import numpy as np
from PIL import Image

from pipeline.mask import make_cross_mask
from pipeline.offset import inverse_offset, offset
from pipeline.repeat import repeat_grid


class PipelineCoreTests(unittest.TestCase):
    def test_offset_and_inverse_restore_pixels(self):
        img = np.arange(4 * 6 * 3, dtype=np.uint8).reshape((4, 6, 3))

        shifted = offset(img, dx=3, dy=2)
        restored = inverse_offset(shifted, dx=3, dy=2)

        np.testing.assert_array_equal(restored, img)

    def test_cross_mask_marks_center_bands(self):
        mask = make_cross_mask((8, 10), width=4)

        self.assertEqual(mask.shape, (8, 10))
        self.assertEqual(mask.dtype, np.uint8)
        self.assertTrue(np.all(mask[:, 3:7] == 255))
        self.assertTrue(np.all(mask[2:6, :] == 255))
        self.assertEqual(mask[0, 0], 0)
        self.assertEqual(mask[-1, -1], 0)

    def test_repeat_grid_tiles_image(self):
        tile = Image.new("RGB", (2, 3), "black")
        tile.putpixel((1, 2), (255, 0, 0))

        repeated = repeat_grid(tile, n=3)

        self.assertEqual(repeated.size, (6, 9))
        self.assertEqual(repeated.getpixel((1, 2)), (255, 0, 0))
        self.assertEqual(repeated.getpixel((3, 5)), (255, 0, 0))
        self.assertEqual(repeated.getpixel((5, 8)), (255, 0, 0))


if __name__ == "__main__":
    unittest.main()
