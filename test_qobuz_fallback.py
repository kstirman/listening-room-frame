import tempfile
import unittest
from unittest.mock import Mock
from bridge import Bridge
from test_auto_catalog import identified_page
from test_catalog_pages import response
from persistent_metadata import Cache


class QobuzFallbackTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cfg = {'qobuz_cache_first': True, 'qobuz_metadata_enabled': True}
        self.app = Bridge(self.cfg, self.tmp.name)
        self.meta = {'object_id':'qobuz/track/2','album_id':'example123','duration_seconds':180}
        self.app.session.get = Mock(return_value=response(identified_page()))

    def test_gap_filled_once_and_retained_on_restart(self):
        metadata = dict(self.meta, artist='Saved artist', album='Saved album')
        first = self.app.details(metadata)
        self.assertEqual(first['display_track'], 'Cycle: No. 2, Part')
        self.assertEqual(first['display_artist'], 'Saved artist')
        self.assertEqual(first['display_album'], 'Saved album')
        self.app.session.get.assert_called_once()
        restarted = Bridge(self.cfg, self.tmp.name)
        restarted.session.get = Mock(side_effect=AssertionError('offline'))
        self.assertEqual(restarted.details(metadata)['display_track'], first['display_track'])
        restarted.session.get.assert_not_called()

    def test_jplay_or_prior_exact_encounter_satisfies_sparse_playback(self):
        album = {'album_id':'example123', 'album':'Example', 'artist':'Player', 'tracks':{'2':{'title':'Saved track','duration_seconds':180}}}
        Cache(self.app.music_catalog.path).write([('jplay','example123',album)])
        self.assertEqual(self.app.details(self.meta)['display_track'], 'Saved track')
        other = dict(self.meta, album_id='other123')
        self.app.music_catalog.encounter(dict(other, title='Other track',artist='Other artist',album='Other album'))
        self.assertEqual(self.app.details(other)['display_track'], 'Other track')
        self.app.session.get.assert_not_called()

    def test_different_edition_cannot_fill_gap(self):
        self.app.session.get.return_value = response(identified_page(sku='another'))
        self.assertNotIn('display_track', self.app.details(self.meta))
