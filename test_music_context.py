import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image
from bridge import Bridge
import layouts
import music_catalog
import music_context
from setup_preview import PreviewBridge


class ContextTests(unittest.TestCase):
    def setUp(self):
        self.meta = {'album': 'Album', 'artist': 'Artist', 'title': 'Track', 'duration_seconds': 200}
        self.row = {'kind': 'influence', 'scope': 'album', 'review_status': 'reviewed',
                    'evidence': 'explicit_attribution', 'target_title': 'Earlier album',
                    'target_artist': 'Earlier artist', 'explanation': 'An explicit account of the connection.',
                    'source_title': 'Artist interview', 'source_url': 'https://example.org/interview',
                    'reviewed_at': '2026-10-31'}
        self.doc = {'albums': [{'album': 'Album', 'artist': 'Artist', 'connections': [self.row]}]}

    def test_exact_identity_and_ambiguity(self):
        self.assertEqual(len(music_context.lookup(self.meta, self.doc)), 1)
        for fields in [{'album': 'Album (Live)'}, {'artist': 'Different artist'}]:
            self.assertEqual(music_context.lookup(dict(self.meta, **fields), self.doc), [])
        self.doc['albums'].append(copy.deepcopy(self.doc['albums'][0]))
        self.assertEqual(music_context.lookup(self.meta, self.doc), [])

    def test_influence_requires_explicit_evidence_and_review(self):
        for fields in [{'evidence': 'documented'}, {'evidence': 'editorial_comparison'},
                       {'review_status': 'draft'}, {'source_url': 'javascript:alert(1)'},
                       {'source_url': 'https://user:password@example.org'}, {'reviewed_at': 'yesterday'},
                       {'explanation': ''}, {'scope': 'unknown'}, {'kind': []}, {'scope': {}}, {'evidence': []}]:
            self.assertIsNone(music_context.clean(dict(self.row, **fields)))
        item = music_context.clean(dict(self.row, kind='comparison', evidence='editorial_comparison'))
        self.assertEqual(item['label'], 'Listening comparison')

    def test_recording_track_and_composition_scopes(self):
        self.row['scope'] = 'recording'
        self.assertEqual(music_context.lookup(self.meta, self.doc), [])
        self.doc['albums'][0]['album_id'] = 'edition1'
        metadata = dict(self.meta, album_id='edition1', object_id='track1')
        self.assertEqual(len(music_context.lookup(metadata, self.doc)), 1)
        self.assertEqual(music_context.lookup(dict(metadata, album_id='edition2'), self.doc), [])
        self.row.update(scope='track', track_id='track2')
        self.assertEqual(music_context.lookup(metadata, self.doc), [])
        self.row['track_id'] = 'track1'
        self.assertEqual(len(music_context.lookup(metadata, self.doc)), 1)
        self.row.update(scope='composition', work='Symphony')
        self.assertEqual(music_context.lookup(metadata, self.doc), [])
        self.assertEqual(len(music_context.lookup(dict(metadata, work='Symphony'), self.doc)), 1)

    def test_malformed_documents(self):
        for doc in [None, [], {'albums': None}, {'albums': [None]}, {'albums': [dict(self.doc['albums'][0], connections=None)]}]:
            self.assertEqual(music_context.lookup(self.meta, doc), [])

    def test_existing_catalog_persists_review_and_bridge_reuses_it(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); path = root/'connections.json'; path.write_text(json.dumps(self.doc))
            b = Bridge({'music_context_file': str(path)}, root/'display')
            b.music_catalog.sync_documents(b.config, root)
            path.unlink()
            details = b.details(self.meta)
            self.assertEqual(details['connections'][0]['target_title'], 'Earlier album')
            self.assertEqual(b.music_catalog.document('music_context'), self.doc)

    def test_console_keeps_sources_and_full_credits(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); b = PreviewBridge({}, root/'display')
            image = root/'preview-source.jpg'; Image.new('RGB', (80,80), 'navy').save(image)
            connections = music_context.lookup(self.meta, self.doc)
            credits = {'personnel': [{'name': 'Player', 'role': 'Bass'}]*12}
            with patch.object(Bridge, 'card', return_value=(image, 'Album')):
                b.card(self.meta, {'connections': connections, 'liner_notes': credits})
            data = json.loads((root/'presentation.json').read_text())
            self.assertEqual(data['connections'][0]['source_url'], self.row['source_url'])
            self.assertEqual(len(data['liner_notes']['personnel']), 12)
            b.publish(image, kind='collage')
            self.assertEqual(json.loads((root/'presentation.json').read_text())['connections'], [])

    def test_cover_is_large_and_unchanged_by_dense_metadata(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); art = root/'art.png'; Image.new('RGB', (100,100), (250,30,40)).save(art)
            output = root/'output.png'
            details = {'composer': 'Composer '*20, 'work': 'Work '*30, 'movement': 'Movement '*40,
                       'conductor': 'Conductor '*20, 'orchestra': 'Orchestra '*20,
                       'duration_seconds': 123, 'composition': {'durations': [123]*16, 'index': 2},
                       'liner_notes': {'personnel': [{'name': 'Name '*20, 'role': 'Role'}]*12},
                       'connections': music_context.lookup(self.meta, self.doc)}
            for render in [lambda: layouts.current(art, 'Album '*50, 'Artist '*50, output),
                           lambda: layouts.liner(art, 'Album '*50, 'Artist '*50, output, details),
                           lambda: layouts.classical(art, details, output),
                           lambda: layouts.roon(art, ['Title '*40]*3, output)]:
                render()
                with Image.open(output) as image:
                    self.assertEqual(image.size, (3840,2160))
                    for point in [(40,40), (2119,40), (40,2119), (2119,2119)]:
                        self.assertEqual(image.getpixel(point), (250,30,40))
