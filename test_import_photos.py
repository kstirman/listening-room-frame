import io
import json
from pathlib import Path
import tempfile
import unittest
from PIL import Image
import import_photos


class ImportTests(unittest.TestCase):
    def row(self, stamp=946684800000, offset=-28800000):
        return ['synthetic-id', ['https://lh3.googleusercontent.com/example', 80, 60], stamp, None, offset, 1, [], [], 2, {}]

    def test_date_is_capture_with_offset_not_upload_time(self):
        self.assertEqual(import_photos.date_taken(self.row()), '1999-12-31')
        self.assertEqual(import_photos.date_taken(self.row(offset=None)), '')
        self.assertEqual(import_photos.date_taken(self.row(offset=99*3600000)), '')

    def test_parse_only_album_media_and_report_partial(self):
        data = [None, [self.row()], 'more-page']
        html = 'AF_initDataCallback({key: \'ds:1\', hash: \'2\', data:'+json.dumps(data)+', sideChannel: {}});'
        self.assertEqual(import_photos.page_data(html), data)
        with self.assertRaises(ValueError): import_photos.page_data('<img src="other-image">')

    def test_import_reuses_cache_preserves_reviewed_place_and_skips_video(self):
        blob = io.BytesIO(); Image.new('RGB', (80,60), 'navy').save(blob,format='JPEG')
        video = self.row(); video[0] = 'synthetic-video'; video[9] = {'76647426':[1000]}
        with tempfile.TemporaryDirectory() as t:
            result = import_photos.import_album([None,[self.row(),video],'more'], t, lambda *args:blob.getvalue())
            self.assertEqual(len(result['photos']),1);self.assertEqual(result['videos_skipped'],1)
            self.assertTrue(result['more_available'])
            result['photos'][0].update(location='Example place',location_source='Reviewed')
            (Path(t)/'photos.json').write_text(json.dumps(result))
            result = import_photos.import_album([None,[self.row()],None], t, lambda *args:self.fail('Cached image downloaded again'))
            self.assertEqual(result['photos'][0]['location'],'Example place')
            before = (Path(t)/'photos.json').read_bytes()
            with self.assertRaises(ValueError): import_photos.import_album([None,[],None],t)
            self.assertEqual((Path(t)/'photos.json').read_bytes(), before)

    def test_unapproved_network_host_rejected(self):
        for url in ['http://lh3.googleusercontent.com/a','https://example.invalid/a','https://user:password@lh3.googleusercontent.com/a']:
            with self.assertRaises(ValueError):import_photos.get(url,100,{'lh3.googleusercontent.com'})
