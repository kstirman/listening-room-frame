import json
import tempfile
import unittest
from unittest.mock import Mock, patch
import requests
from bridge import Bridge

class CaptionIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.bridge = Bridge({}, self.temp.name)
        self.bridge.album_page = Mock(side_effect=requests.RequestException('unavailable'))
        self.metadata = {'album_id': 'abc123', 'object_id': 'qobuz/track/123', 'duration_seconds': 120}

    def save(self, **changes):
        payload = dict(album_id='abc123', source='qobuz', album='Saved Album', artist='Saved Artist',
                       _album_identity_fields=['album', 'artist'])
        payload.update(changes)
        with self.bridge.music_catalog.connect() as db:
            db.execute('INSERT OR REPLACE INTO encountered_albums(identity,album_id,first_seen,last_seen,payload) VALUES(?,?,?,?,?)',
                       ('qobuz:abc123', 'abc123', 1, 1, json.dumps(payload)))

    def test_sparse_album_has_captions_without_track_claim(self):
        self.save(title='Wrong Track', work='Wrong Work', compositionDate='1900')
        result = self.bridge.details(self.metadata)
        self.assertEqual(result['display_album'], 'Saved Album')
        self.assertEqual(result['display_artist'], 'Saved Artist')
        for field in ('display_track','work','composer','composition_year','recording_year','movement','part'):
            self.assertNotIn(field, result)

    def test_current_names_win(self):
        self.save()
        result = self.bridge.details(dict(self.metadata, album='Current Album', artist='Current Artist'))
        self.assertEqual(result['display_album'], 'Current Album')
        self.assertEqual(result['display_artist'], 'Current Artist')

    def test_reviewed_edition_wins(self):
        self.save()
        with patch.object(self.bridge, 'reviewed_edition_album', return_value={'album':'Verified Album','artist':'Verified Artist'}):
            result = self.bridge.details(self.metadata)
        self.assertEqual(result['display_album'], 'Verified Album')
        self.assertEqual(result['display_artist'], 'Verified Artist')

    def test_unproven_names_not_displayed(self):
        self.save(source='')
        result = self.bridge.details(self.metadata)
        self.assertNotIn('display_album', result)
        self.assertNotIn('display_artist', result)

    def test_new_caption_not_hidden_by_cached_blank_result(self):
        self.bridge.album_page = Mock(return_value='<html></html>')
        before = self.bridge.details(self.metadata)
        self.assertNotIn('display_album', before)
        self.save()
        after = self.bridge.details(self.metadata)
        self.assertEqual(after['display_album'], 'Saved Album')
        self.assertFalse(self.bridge.detail_cache)

    def test_card_uses_fallback_album_and_artist(self):
        self.save()
        details = self.bridge.details(self.metadata)
        self.bridge.state.update(album=self.bridge.album_key(self.metadata), history=[{'key':self.bridge.album_key(self.metadata),'cover':'cover.jpg','title':'','artist':''}])
        self.bridge.cover = Mock(return_value=('cover.jpg', ''))
        with patch('bridge.layouts.current') as draw:
            self.bridge.card(self.metadata, details)
        self.assertEqual(draw.call_args.args[1:3], ('Saved Album','Saved Artist'))

if __name__ == '__main__': unittest.main()
