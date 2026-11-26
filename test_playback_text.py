import unittest
from unittest.mock import patch

import layouts
from playback_text import (MAX_PLAYBACK_TEXT_UTF8_BYTES, MAX_TEXT_CODEPOINTS,
                           MAX_TEXT_UTF8_BYTES, limit_text, limit_texts)


class LengthDraw:
    def __init__(self):
        self.calls = 0

    def textlength(self, text, font=None):
        self.calls += 1
        return len(text)


class PlaybackTextTests(unittest.TestCase):
    def test_field_and_aggregate_limits_preserve_normal_text(self):
        self.assertEqual(limit_text('I. Allegro · Example Orchestra'),
                         'I. Allegro · Example Orchestra')
        latin = limit_text('x' * (MAX_TEXT_CODEPOINTS + 1))
        unicode = limit_text('🎵' * MAX_TEXT_CODEPOINTS)
        self.assertTrue(latin.endswith('…'))
        self.assertTrue(unicode.endswith('…'))
        self.assertLessEqual(len(latin), MAX_TEXT_CODEPOINTS)
        self.assertLessEqual(len(unicode.encode('utf-8')), MAX_TEXT_UTF8_BYTES)
        fields = limit_texts(['x' * MAX_TEXT_CODEPOINTS] * 20)
        self.assertLessEqual(sum(len(value.encode('utf-8')) for value in fields),
                             MAX_PLAYBACK_TEXT_UTF8_BYTES)

    def test_fitting_uses_bounded_measurements(self):
        draw = LengthDraw()
        with patch('layouts.font', return_value=object()):
            text, _ = layouts.fit_text(draw, 'x' * 1_000_000, 10, 34)
        self.assertEqual(text, 'x' * 9 + '…')
        self.assertLess(draw.calls, 16)


if __name__ == '__main__':
    unittest.main()
