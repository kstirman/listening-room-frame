"""Bounded decoding and normalization for album artwork."""
import warnings

from PIL import Image, ImageOps, UnidentifiedImageError


SUPPORTED_COVER_FORMATS = ('JPEG', 'PNG', 'WEBP')
MAX_COVER_DIMENSION = 4096
MAX_COVER_PIXELS = MAX_COVER_DIMENSION * MAX_COVER_DIMENSION
MAX_COVER_DECODED_BYTES = 48 * 1024 * 1024
NORMALIZED_COVER_SIZE = (1600, 1600)

_MODE_BYTES = {
    '1': 1, 'L': 1, 'P': 1,
    'LA': 2, 'La': 2, 'PA': 2,
    'I;16': 2, 'I;16L': 2, 'I;16B': 2, 'I;16N': 2,
    'RGB': 3, 'YCbCr': 3,
    'RGBA': 4, 'RGBa': 4, 'RGBX': 4, 'CMYK': 4, 'I': 4, 'F': 4,
}


def _validate_header(image):
    if image.format not in SUPPORTED_COVER_FORMATS:
        raise ValueError('Unsupported cover image format')
    width, height = image.size
    if (width < 1 or height < 1 or
            width > MAX_COVER_DIMENSION or height > MAX_COVER_DIMENSION or
            width * height > MAX_COVER_PIXELS):
        raise ValueError('Cover dimensions exceed safety limit')
    if getattr(image, 'n_frames', 1) != 1:
        raise ValueError('Animated covers are not supported')
    mode_bytes = _MODE_BYTES.get(image.mode)
    if mode_bytes is None:
        raise ValueError('Unsupported cover image mode')
    # Conversion to RGB needs at least three bytes per pixel even for palette
    # and grayscale inputs; retain the larger source representation otherwise.
    if width * height * max(3, mode_bytes) > MAX_COVER_DECODED_BYTES:
        raise ValueError('Cover decoded size exceeds safety limit')


def normalized_cover(source):
    """Decode one bounded cover and return a detached 1600px RGB image."""
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            previous_limit = Image.MAX_IMAGE_PIXELS
            try:
                # Pillow performs its bomb check while opening the lazy image.
                Image.MAX_IMAGE_PIXELS = MAX_COVER_PIXELS
                image = Image.open(source, formats=list(SUPPORTED_COVER_FORMATS))
            finally:
                Image.MAX_IMAGE_PIXELS = previous_limit
            with image:
                _validate_header(image)
                image.load()
                # Shrink before EXIF transposition so orientation cannot create
                # another full-size raster; the square bound is orientation-safe.
                image.thumbnail(NORMALIZED_COVER_SIZE, Image.Resampling.LANCZOS)
                return ImageOps.exif_transpose(image).convert('RGB')
    except (Image.DecompressionBombWarning, Image.DecompressionBombError,
            UnidentifiedImageError, OSError, SyntaxError, EOFError) as error:
        raise ValueError('Unsupported or unsafe cover image') from error
