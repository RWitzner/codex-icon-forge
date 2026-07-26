"""Tests for chroma-extract helpers.

Covers the alpha-only erode + blur edge cleanup pass that suppresses the
anti-aliased halo around silhouettes on a chroma-key background. RGB must
stay untouched; only alpha is reshaped.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

SKILL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL_DIR))

from engine.extractors._helpers import (  # noqa: E402
    fit_to_cell,
    remove_chroma_background,
)

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


class DarkFringeTests(unittest.TestCase):
    """The blur must not resurrect alpha on pixels whose RGB was zeroed.

    The sibling tests in this file use a pure-black disc and skip pixels where
    red == 0 and blue == 0 as "legitimate black silhouette" — which makes them
    structurally blind to a black halo. These use a saturated silhouette, so
    any near-black partial-alpha pixel can only be the artifact.
    """

    KEY = (255, 0, 255)
    SILHOUETTE = (250, 215, 60, 255)  # saturated yellow

    def _sprite(self) -> Image.Image:
        image = Image.new("RGBA", (200, 200), (*self.KEY, 255))
        ImageDraw.Draw(image).ellipse((40, 40, 160, 160), fill=self.SILHOUETTE)
        return image

    @staticmethod
    def _dark_fringe(image: Image.Image) -> tuple[int, int]:
        pixels = image.load()
        dark = partial = 0
        for y in range(image.height):
            for x in range(image.width):
                red, green, blue, alpha = pixels[x, y]
                if 0 < alpha < 255:
                    partial += 1
                    if 0.299 * red + 0.587 * green + 0.114 * blue < 32:
                        dark += 1
        return dark, partial

    def test_no_near_black_pixels_survive_along_the_edge(self) -> None:
        cleaned = remove_chroma_background(
            self._sprite(),
            self.KEY,
            96.0,
            alpha_erode_px=1,
            alpha_blur_radius=1.0,
        )
        dark, partial = self._dark_fringe(cleaned)
        self.assertGreater(partial, 0, "fixture produced no soft edge at all")
        self.assertEqual(
            dark,
            0,
            f"{dark} of {partial} partial-alpha pixels are near-black; the "
            "alpha blur is reviving pixels whose RGB was zeroed",
        )

    def test_edge_composites_close_to_the_silhouette_colour(self) -> None:
        """The real requirement: the edge must look like the artwork.

        Counting near-black pixels only catches the failure once it is total.
        Compositing the result against white and black and comparing to an
        ideal built from the same alpha with the true colour measures how far
        the fringe is from correct, whatever direction it is wrong in.
        """

        cleaned = remove_chroma_background(
            self._sprite(), self.KEY, 96.0, alpha_erode_px=1, alpha_blur_radius=1.0
        )
        ideal = Image.merge(
            "RGBA",
            (
                *Image.new("RGB", cleaned.size, self.SILHOUETTE[:3]).split(),
                cleaned.getchannel("A"),
            ),
        )
        for backdrop in ((255, 255, 255), (0, 0, 0)):
            plate_a = Image.new("RGBA", cleaned.size, (*backdrop, 255))
            plate_a.alpha_composite(cleaned)
            plate_b = Image.new("RGBA", cleaned.size, (*backdrop, 255))
            plate_b.alpha_composite(ideal)
            worst = max(
                max(abs(x - y) for x, y in zip(p, q))
                for p, q in zip(
                    plate_a.convert("RGB").getdata(), plate_b.convert("RGB").getdata()
                )
            )
            self.assertLess(
                worst, 24, f"fringe is {worst}/255 off the silhouette on {backdrop}"
            )

    def test_opaque_silhouette_rgb_is_never_overwritten(self) -> None:
        """Guards the solid-interior restore; without it the artwork blurs."""

        image = Image.new("RGBA", (200, 200), (*self.KEY, 255))
        draw = ImageDraw.Draw(image)
        draw.ellipse((40, 40, 160, 160), fill=(255, 140, 30, 255))
        draw.rectangle((95, 60, 105, 140), fill=(0, 60, 255, 255))
        cleaned = remove_chroma_background(
            image, self.KEY, 96.0, alpha_erode_px=1, alpha_blur_radius=1.0
        )

        source = image.load()
        result = cleaned.load()
        for y in range(image.height):
            for x in range(image.width):
                if result[x, y][3] == 255:
                    self.assertEqual(
                        result[x, y][:3],
                        source[x, y][:3],
                        f"opaque pixel at {(x, y)} was rewritten",
                    )

    def test_transparent_pixels_carry_no_colour(self) -> None:
        """fit_to_cell's getbbox() depends on this, as does WebP encoding."""

        cleaned = remove_chroma_background(
            self._sprite(), self.KEY, 96.0, alpha_erode_px=1, alpha_blur_radius=1.0
        )
        pixels = cleaned.load()
        for y in range(cleaned.height):
            for x in range(cleaned.width):
                red, green, blue, alpha = pixels[x, y]
                if alpha == 0:
                    self.assertEqual((red, green, blue), (0, 0, 0))

    def test_blur_still_softens_the_edge(self) -> None:
        """Clamping must not degenerate into skipping the blur entirely."""

        sprite = self._sprite()
        unblurred = remove_chroma_background(
            sprite, self.KEY, 96.0, alpha_erode_px=1, alpha_blur_radius=0.0
        )
        blurred = remove_chroma_background(
            sprite, self.KEY, 96.0, alpha_erode_px=1, alpha_blur_radius=1.0
        )
        _, partial_unblurred = self._dark_fringe(unblurred)
        _, partial_blurred = self._dark_fringe(blurred)
        self.assertGreater(
            partial_blurred,
            partial_unblurred,
            "clamped blur should still produce intermediate alpha values",
        )


class FitToCellTests(unittest.TestCase):
    """Framing has to be a property of the design, not of the output size."""

    @staticmethod
    def _sprite(width: int, height: int, canvas: int = 1200) -> Image.Image:
        image = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0))
        left = (canvas - width) // 2
        top = (canvas - height) // 2
        block = Image.new("RGBA", (width, height), (20, 160, 90, 255))
        image.alpha_composite(block, (left, top))
        return image

    @staticmethod
    def _largest_axis_fill(image: Image.Image, cell: int) -> float:
        alpha = image.getchannel("A").point(lambda v: 255 if v > 128 else 0)
        bbox = alpha.getbbox()
        assert bbox is not None
        return max((bbox[2] - bbox[0]) / cell, (bbox[3] - bbox[1]) / cell)

    def test_padding_is_a_parameter_the_validator_can_agree_with(self) -> None:
        sprite = self._sprite(1100, 1100)
        for padding in (10, 40):
            fitted = fit_to_cell(sprite, 512, 512, padding_px=padding)
            alpha = fitted.getchannel("A").point(lambda v: 255 if v > 128 else 0)
            bbox = alpha.getbbox()
            assert bbox is not None
            self.assertLessEqual(bbox[2] - bbox[0], 512 - padding + 1)

    def test_fitting_once_then_downscaling_keeps_framing_constant(self) -> None:
        """Fitting per size applies a constant pixel pad to different cells."""

        sprite = self._sprite(1100, 900)
        sizes = [128, 256, 512, 1024]

        per_size = [
            self._largest_axis_fill(fit_to_cell(sprite, size, size), size)
            for size in sizes
        ]
        self.assertGreater(
            max(per_size) - min(per_size),
            0.03,
            "fixture no longer reproduces the per-size framing drift",
        )

        master = fit_to_cell(sprite, 1024, 1024)
        downscaled = [
            self._largest_axis_fill(
                master if size == 1024 else master.resize(
                    (size, size), Image.Resampling.LANCZOS
                ),
                size,
            )
            for size in sizes
        ]
        self.assertLess(max(downscaled) - min(downscaled), 0.02)

    def test_allow_upscale_normalises_a_small_design_to_its_packmates(self) -> None:
        big = self._sprite(1000, 1000)
        small = self._sprite(700, 700)

        clamped = [
            self._largest_axis_fill(fit_to_cell(s, 1024, 1024), 1024)
            for s in (big, small)
        ]
        self.assertGreater(
            clamped[0] - clamped[1],
            0.1,
            "fixture no longer reproduces the unnormalised-sibling case",
        )

        normalised = [
            self._largest_axis_fill(
                fit_to_cell(s, 1024, 1024, allow_upscale=True), 1024
            )
            for s in (big, small)
        ]
        self.assertLess(abs(normalised[0] - normalised[1]), 0.02)

    def test_extraction_still_refuses_to_upscale_by_default(self) -> None:
        small = self._sprite(400, 400)
        fitted = fit_to_cell(small, 1024, 1024)
        alpha = fitted.getchannel("A").point(lambda v: 255 if v > 128 else 0)
        bbox = alpha.getbbox()
        assert bbox is not None
        self.assertEqual(bbox[2] - bbox[0], 400)


if __name__ == "__main__":
    unittest.main(verbosity=2)
