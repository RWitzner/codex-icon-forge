"""Tests for chroma-extract helpers.

Covers the alpha-only erode + blur edge cleanup pass that suppresses the
anti-aliased halo around silhouettes on a chroma-key background. RGB must
stay untouched; only alpha is reshaped.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

from PIL import Image

SKILL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL_DIR))

from engine.extractors._helpers import remove_chroma_background  # noqa: E402

MAGENTA = (255, 0, 255)


def _solid(width: int, height: int, color: tuple[int, int, int, int]) -> Image.Image:
    return Image.new("RGBA", (width, height), color)


def _composite_silhouette_on_chroma(
    cell_width: int, cell_height: int
) -> Image.Image:
    """Black anti-aliased disc on a magenta background.

    Mimics what ``$imagegen`` produces for a clean-app-icon style: solid
    chroma background, dark foreground, anti-aliased fringe pixels along
    the silhouette boundary.
    """

    image = Image.new("RGBA", (cell_width, cell_height), MAGENTA + (255,))
    pixels = image.load()
    cx, cy = cell_width / 2, cell_height / 2
    radius = min(cx, cy) * 0.6
    for y in range(cell_height):
        for x in range(cell_width):
            dx, dy = x - cx + 0.5, y - cy + 0.5
            distance = (dx * dx + dy * dy) ** 0.5
            if distance <= radius - 1.0:
                pixels[x, y] = (0, 0, 0, 255)
            elif distance <= radius:
                # Anti-aliased fringe: blend between black and magenta
                weight = radius - distance
                blend = max(0.0, min(1.0, weight))
                red = int(255 * (1 - blend))
                blue = int(255 * (1 - blend))
                pixels[x, y] = (red, 0, blue, 255)
    return image


class ChromaEdgeCleanupTests(unittest.TestCase):
    def test_solid_chroma_pixels_become_transparent(self) -> None:
        image = _solid(32, 32, MAGENTA + (255,))
        cleaned = remove_chroma_background(
            image,
            MAGENTA,
            threshold=96.0,
            alpha_erode_px=0,
            alpha_blur_radius=0,
        )
        self.assertEqual(cleaned.getpixel((0, 0))[3], 0)
        self.assertEqual(cleaned.getpixel((16, 16))[3], 0)

    def test_pure_black_pixels_keep_rgb_after_cleanup(self) -> None:
        image = _composite_silhouette_on_chroma(64, 64)
        cleaned = remove_chroma_background(image, MAGENTA, threshold=96.0)
        # Centre is pure black, far from magenta — RGB must survive both
        # the threshold pass and the alpha-only cleanup pass.
        red, green, blue, alpha = cleaned.getpixel((32, 32))
        self.assertEqual((red, green, blue), (0, 0, 0))
        self.assertEqual(alpha, 255)

    def test_erode_blur_removes_halo_pixels(self) -> None:
        """Pixels just outside the hard threshold must end up transparent."""

        image = _composite_silhouette_on_chroma(128, 128)
        without_cleanup = remove_chroma_background(
            image,
            MAGENTA,
            threshold=96.0,
            alpha_erode_px=0,
            alpha_blur_radius=0,
        )
        with_cleanup = remove_chroma_background(image, MAGENTA, threshold=96.0)

        # Count surviving "halo" pixels: any non-transparent pixel whose RGB
        # is magenta-tinted rather than the legitimate pure-black silhouette.
        # Silhouette is (0, 0, 0); the fringe band carries non-zero red and
        # blue from the anti-aliased magenta blend.
        def halo_pixel_count(img: Image.Image) -> int:
            count = 0
            data = img.load()
            for y in range(img.height):
                for x in range(img.width):
                    red, _green, blue, alpha = data[x, y]
                    if alpha == 0:
                        continue
                    if red == 0 and blue == 0:
                        continue  # legitimate black silhouette
                    count += 1
            return count

        before = halo_pixel_count(without_cleanup)
        after = halo_pixel_count(with_cleanup)
        self.assertGreater(before, 0, "test fixture should have halo pixels")
        self.assertLess(after, before)

    def test_alpha_band_is_softened(self) -> None:
        """After blur, the alpha edge has at least some intermediate values."""

        image = _composite_silhouette_on_chroma(128, 128)
        cleaned = remove_chroma_background(image, MAGENTA, threshold=96.0)
        alpha_values = set()
        data = cleaned.load()
        for y in range(cleaned.height):
            for x in range(cleaned.width):
                alpha_values.add(data[x, y][3])
        intermediate = [a for a in alpha_values if 0 < a < 255]
        self.assertGreater(
            len(intermediate),
            0,
            "Gaussian blur on alpha should produce intermediate values",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
