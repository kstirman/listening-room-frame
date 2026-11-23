import io
import unittest
from unittest.mock import Mock, patch

from PIL import Image

from cover_images import (MAX_COVER_DECODED_BYTES, MAX_COVER_DIMENSION,
                          normalized_cover)


class CoverImageTests(unittest.TestCase):
    @staticmethod
    def lazy_image(**changes):
        image = Mock(format='JPEG', size=(100, 100), mode='RGB', n_frames=1)
        for name, value in changes.items():
            setattr(image, name, value)
        image.__enter__ = Mock(return_value=image)
        image.__exit__ = Mock(return_value=False)
        return image

    def test_oversized_dimensions_rejected_before_decode(self):
        image = self.lazy_image(size=(MAX_COVER_DIMENSION + 1, 100))
        with patch('cover_images.Image.open', return_value=image):
            with self.assertRaisesRegex(ValueError, 'dimensions'):
                normalized_cover(io.BytesIO(b'compressed image'))
        image.load.assert_not_called()

    def test_decoded_memory_rejected_before_decode(self):
        side = int((MAX_COVER_DECODED_BYTES // 4) ** .5) + 1
        image = self.lazy_image(size=(side, side), mode='RGBA')
        with patch('cover_images.Image.open', return_value=image):
            with self.assertRaisesRegex(ValueError, 'decoded size'):
                normalized_cover(io.BytesIO(b'compressed image'))
        image.load.assert_not_called()

    def test_multiframe_cover_rejected_before_decode(self):
        image = self.lazy_image(format='WEBP', n_frames=2)
        with patch('cover_images.Image.open', return_value=image):
            with self.assertRaisesRegex(ValueError, 'Animated'):
                normalized_cover(io.BytesIO(b'compressed image'))
        image.load.assert_not_called()

    def test_supported_cover_preserves_orientation_and_normalizes(self):
        data = io.BytesIO()
        exif = Image.Exif()
        exif[274] = 6
        Image.new('RGB', (2000, 1000), 'navy').save(data, format='JPEG', exif=exif)
        data.seek(0)
        previous_limit = Image.MAX_IMAGE_PIXELS
        with normalized_cover(data) as image:
            self.assertEqual(image.mode, 'RGB')
            self.assertEqual(image.size, (800, 1600))
        self.assertEqual(Image.MAX_IMAGE_PIXELS, previous_limit)

    def test_unlisted_format_rejected(self):
        data = io.BytesIO()
        Image.new('RGB', (20, 20), 'navy').save(data, format='GIF')
        data.seek(0)
        with self.assertRaisesRegex(ValueError, 'unsafe'):
            normalized_cover(data)


if __name__ == '__main__':
    unittest.main()
