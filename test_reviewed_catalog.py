import tempfile
import unittest
from unittest.mock import Mock
from bridge import Bridge
from reviewed_catalog import lookup


MAPPING = {'album': 'Example Cycle', 'artist': 'Example Choir', 'qobuz_album_id': 'fixture'}
META = {'album': 'Example Cycle', 'artist': 'Example Choir', 'title': 'Cycle: No. 2, Second', 'duration_seconds': 122}


def page():
    return '<div id="playerTracks">' + ''.join(
        f'<div data-track="{i}" data-gtm=\'{{"product":{{"category":"classique"}}}}\'><span class="track__item--name">Cycle: No. {i}, {name}</span><p class="track__info">Example Composer, Composer</p><span class="track__item--duration">00:02:0{i}</span></div>'
        for i, name in [(1, 'First'), (2, 'Second'), (3, 'Third')]) + '</div>'


class ReviewedCatalogTests(unittest.TestCase):
    def test_verified_album_track_and_duration_match(self):
        row, context = lookup(META, [MAPPING], lambda _: page())
        self.assertEqual(row['work'], 'Cycle')
        self.assertEqual(context, {'index': 1, 'durations': [121,122,123], 'label': 'Part'})

    def test_other_album_artist_and_invalid_mapping_do_not_fetch(self):
        fetch = Mock()
        for meta in [dict(META, album='Other'), dict(META, artist='Other'), dict(META, duration_seconds=None)]:
            self.assertIsNone(lookup(meta, [MAPPING], fetch))
        for mappings in [[], [MAPPING, MAPPING], [dict(MAPPING, qobuz_album_id='../x')]]:
            self.assertIsNone(lookup(META, mappings, fetch))
        fetch.assert_not_called()

    def test_mismatched_duration_or_ambiguous_track_omits_context(self):
        self.assertIsNone(lookup(dict(META, duration_seconds=125), [MAPPING], lambda _: page()))
        self.assertIsNone(lookup(META, [MAPPING], lambda _: page()+page().replace('data-track="', 'data-track="x')))

    def test_bridge_handles_reused_proxy_id_and_catalog_failure(self):
        with tempfile.TemporaryDirectory() as root:
            b = Bridge({'reviewed_catalog_albums':[MAPPING]}, root)
            b.album_page = Mock(return_value=page())
            d = b.details(dict(META, object_id='13'))
            self.assertEqual(d['composition']['index'], 1)
            other = b.details(dict(META, object_id='13', album='Different'))
            self.assertNotIn('composition', other)
        with tempfile.TemporaryDirectory() as root:
            b = Bridge({'reviewed_catalog_albums':[MAPPING]}, root)
            b.album_page = Mock(side_effect=RuntimeError('offline'))
            self.assertEqual(b.details(META)['display_track'], META['title'])
