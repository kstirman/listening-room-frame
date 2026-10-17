import io
import tempfile
import unittest
from unittest.mock import Mock, patch
from PIL import Image
from bridge import Bridge

class AudirvanaCoverTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.bridge = Bridge({'local_art_hosts': ['192.0.2.10']}, self.tmp.name)
        self.bridge.session = Mock()
        image = io.BytesIO()
        Image.new('RGB', (16, 16), 'blue').save(image, format='JPEG')
        self.bridge.session.get.return_value.content = image.getvalue()

    def test_qobuz_cover_without_album_id(self):
        url = 'https://static.qobuz.com/images/covers/00/00/example_600.jpg'
        metadata = {'album': 'Example', 'artist': 'Example Artist',
                    'resource': 'http://192.0.2.10/audirvana/audio_f_1.wav',
                    'albumArtURI': url}
        with patch('bridge.layouts.current'):
            self.bridge.cover(metadata)
        self.bridge.session.get.assert_called_once_with(url, timeout=15)
        self.assertEqual(self.bridge.state['history'][0]['title'], 'Example')

    def test_reject_untrusted_or_noncover_urls(self):
        for url in ['https://static.qobuz.com.evil.test/images/covers/x.jpg',
                    'https://static.qobuz.com/audio/x.wav',
                    'http://static.qobuz.com/images/covers/x.jpg',
                    'https://user@static.qobuz.com/images/covers/x.jpg',
                    'https://static.qobuz.com:8443/images/covers/x.jpg', '']:
            with self.subTest(url=url), self.assertRaises(ValueError):
                self.bridge.cover({'albumArtURI': url,
                                   'resource': 'https://stream.example/audio.wav'})
        self.bridge.session.get.assert_not_called()
