import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from PIL import Image
from bridge import Bridge
import idle_photos
import layouts


class PhotoTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        Image.new('RGB', (80, 120), 'navy').save(self.root/'one.jpg')
        Image.new('RGB', (120, 80), 'tan').save(self.root/'two.jpg')
        self.manifest = self.root/'photos.json'
        self.manifest.write_text(json.dumps({'photos': [{'file':'one.jpg'}, {'file':'two.jpg'}]}))
        self.b = Bridge({'idle_seconds':120, 'idle_photos_enabled':True,
                         'idle_photos_file':str(self.manifest), 'idle_photo_interval_seconds':60}, self.root/'state')
        self.b.state.update(original='ORIGINAL', owned='ALBUM', album_image='ALBUM',
                            album='qobuz:a', view='album')
        self.tv = Mock()
        self.current = 'ALBUM'
        self.tv.get_artmode.return_value = 'on'
        self.tv.get_current.side_effect = lambda: {'content_id':self.current}
        self.tv.select_image.side_effect = lambda c: setattr(self, 'current', c)
        self.tv.upload.side_effect = ['PHOTO1', 'PHOTO2', 'RESUMED']
        self.b.television = Mock(return_value=self.tv)
        self.b.playback = Mock(return_value=('STOPPED', {}))
        self.b.details = Mock(return_value=None)
        self.b.card = Mock(return_value=(self.root/'one.jpg', 'Album'))
        self.b.stopped_at = 0
        self.b.next_collage = 99999

    def tearDown(self):
        self.temp.cleanup()

    def step(self, now):
        with patch('bridge.time.monotonic', return_value=now), patch('bridge.layouts.photo'):
            self.b.step()

    def test_grace_rotation_resume_and_restore(self):
        self.step(119); self.tv.upload.assert_not_called()
        self.step(120); self.assertEqual(self.current, 'PHOTO1')
        self.step(179); self.assertEqual(self.tv.upload.call_count, 1)
        self.step(180); self.assertEqual(self.current, 'PHOTO2')
        self.tv.delete.assert_called_with('PHOTO1')
        self.b.playback.return_value = ('PLAYING', {'album_id':'a'})
        self.step(181); self.assertEqual(self.current, 'RESUMED')
        self.assertNotIn('photo_image', self.b.state)
        self.assertEqual(self.b.state['original'], 'ORIGINAL')
        self.b.restore(); self.assertEqual(self.current, 'ORIGINAL')

    def test_manual_override_survives_idle_and_restart(self):
        self.step(120); self.current = 'MANUAL'; self.step(151)
        self.step(400); self.assertEqual(self.current, 'MANUAL')
        restarted = Bridge(self.b.config, self.root/'state')
        self.assertTrue(restarted.state['idle_photo_blocked'])
        self.b.restore(); self.assertEqual(self.current, 'MANUAL')
        self.assertNotIn('MANUAL', [c.args[0] for c in self.tv.delete.call_args_list])

    def test_tv_not_in_art_mode(self):
        self.tv.get_artmode.return_value = 'off'
        self.step(120); self.tv.upload.assert_not_called(); self.tv.select_image.assert_not_called()

    def test_unknown_transport_never_starts_photos(self):
        self.b.playback.return_value = ('TRANSITIONING', {})
        self.step(120); self.tv.upload.assert_not_called()

    def test_empty_manifest_restores_original(self):
        self.manifest.write_text('{"photos":[]}')
        self.step(120); self.assertEqual(self.current, 'ORIGINAL')

    def test_upload_failure_retains_album(self):
        self.tv.upload.side_effect = ConnectionError('failed')
        with self.assertRaises(ConnectionError): self.step(120)
        self.assertEqual(self.current, 'ALBUM'); self.assertEqual(self.b.state['owned'], 'ALBUM')

    def test_pending_photo_selection_restores_after_response_loss(self):
        def lost(c):
            self.current = c
            raise ConnectionError('response lost')
        self.tv.select_image.side_effect = lost
        with self.assertRaises(ConnectionError): self.step(120)
        self.assertEqual(self.b.state['pending'], 'PHOTO1')
        self.tv.select_image.side_effect = lambda c: setattr(self, 'current', c)
        self.b.restore(); self.assertEqual(self.current, 'ORIGINAL')

    def test_restart_from_photo_resumes_same_album_immediately(self):
        self.step(120)
        restarted = Bridge(self.b.config, self.root/'state')
        restarted.television = self.b.television
        restarted.playback = Mock(return_value=('PLAYING', {'album_id':'a'}))
        restarted.details = self.b.details; restarted.card = self.b.card
        restarted.step()
        self.assertEqual(self.current, 'PHOTO2')  # second mock upload is the music card
        self.assertEqual(restarted.state['view'], 'album')
        self.assertNotIn('photo_image', restarted.state)

    def test_invalid_manifest_and_path_escape(self):
        for value in ([], None, {'photos':None}, {'photos':[None, {'file':'../escape.jpg'}, {'file':'/etc/passwd'}]}):
            self.manifest.write_text(json.dumps(value))
            self.assertEqual(idle_photos.load(self.b.config), [])

    def test_only_sourced_date_and_place(self):
        self.assertEqual(idle_photos.caption({'date':'2001-01-01','location':'Place'}), '')
        self.assertEqual(idle_photos.caption({'date':'2001-01-01','date_source':'EXIF',
                                             'location':'Place','location_source':'Reviewed'}), 'January 1, 2001  ·  Place')
        self.assertEqual(idle_photos.caption({'date':'bad','date_source':'EXIF'}), '')

    def test_photo_render(self):
        for name in ('one.jpg', 'two.jpg'):
            layouts.photo(self.root/name, 'January 1, 2001  ·  Example place', self.root/'out.jpg')
            with Image.open(self.root/'out.jpg') as image:
                self.assertEqual(image.size, (3840, 2160))
