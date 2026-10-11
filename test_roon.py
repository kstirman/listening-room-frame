import io
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock
from PIL import Image
from bridge import Bridge
from roon_source import RoonSource


class RoonTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.source = self.root / 'roon'
        self.source.mkdir()
        self.data = {'version': 1, 'updated_at': time.time(), 'status': 'PLAYING',
                     'album_key': 'roon:' + 'a'*64, 'track_key': 'roon:' + 'b'*64,
                     'cover': 'c'*64 + '.jpg', 'lines': ['I. Allegro', 'Artist', 'Album']}
        Image.new('RGB', (100, 100), 'navy').save(self.source / self.data['cover'])
        self.write()
        self.b = Bridge({'source': 'roon', 'roon_state_dir': str(self.source), 'idle_seconds': 0}, self.root / 'display')
        self.b.soap = Mock(side_effect=AssertionError('UPnP called'))
        self.b.session.get = Mock(side_effect=AssertionError('HTTP called'))

    def tearDown(self):
        self.tmp.cleanup()

    def write(self):
        (self.source / 'now-playing.json').write_text(json.dumps(self.data))

    def test_render_without_upnp_minimserver_or_qobuz(self):
        status, m = self.b.playback()
        self.assertEqual(status, 'PLAYING')
        self.assertEqual(self.b.album_key(m), self.data['album_key'])
        details = self.b.details(m)
        self.assertNotIn('composer', details)
        card, title = self.b.card(m, details)
        self.assertEqual(title, 'Album')
        with Image.open(card) as im:
            self.assertEqual(im.size, (3840, 2160))
        self.b.soap.assert_not_called()
        self.b.session.get.assert_not_called()

    def test_stale_missing_invalid_snapshot_is_stopped(self):
        for changes in ({'updated_at':time.time()-40}, {'updated_at':time.time()+60},
                        {'version':2}, {'lines':None}, {'album_key':'qobuz:wrong'}):
            original = dict(self.data)
            self.data.update(changes);self.write()
            self.assertEqual(self.b.playback(), ('STOPPED', {}))
            self.data = original
        (self.source / 'now-playing.json').write_text('{')
        self.assertEqual(self.b.playback(), ('STOPPED', {}))
        (self.source / 'now-playing.json').unlink()
        self.assertEqual(self.b.playback(), ('STOPPED', {}))

    def test_pause_no_art_and_path_traversal(self):
        self.data['status']='PAUSED_PLAYBACK';self.write()
        self.assertEqual(self.b.playback(), ('PAUSED_PLAYBACK', {}))
        self.data['status']='PLAYING'
        for cover in ('', '../secret', '/tmp/secret'):
            self.data['cover']=cover;self.write()
            self.assertEqual(self.b.playback(), ('PLAYING', {}))

    def test_changed_track_refreshes_same_album_display(self):
        _, m = self.b.playback(); first=self.b.details(m)
        self.data['lines'][0]='II. Andante';self.data['track_key']='roon:'+'d'*64;self.write()
        _, second=self.b.playback()
        self.assertEqual(self.b.album_key(m), self.b.album_key(second))
        self.assertNotEqual(first,self.b.details(second))

    def test_tv_updates_on_track_change_and_restores_on_stale_source(self):
        tv = Mock()
        current = ['ORIGINAL']
        tv.get_artmode.return_value = 'on'
        tv.get_current.side_effect = lambda: {'content_id': current[0]}
        tv.select_image.side_effect = lambda content: current.__setitem__(0, content)
        tv.upload.side_effect = ['CARD1', 'CARD2']
        self.b.television = Mock(return_value=tv)
        self.b.step()
        self.assertEqual(current[0], 'CARD1')
        self.data['lines'][0] = 'II. Andante'
        self.data['track_key'] = 'roon:' + 'd'*64
        self.write()
        self.b.step()
        self.assertEqual(current[0], 'CARD2')
        self.assertEqual(len(self.b.state['history']), 1)
        self.data['updated_at'] = time.time()-40
        self.write()
        self.b.step()
        self.assertEqual(current[0], 'ORIGINAL')
        self.b.soap.assert_not_called()

    def test_unknown_source_rejected(self):
        with self.assertRaises(ValueError): Bridge({'source':'typo'}, self.root/'bad')

    def test_symlink_art_rejected(self):
        p=self.source/self.data['cover'];p.unlink();p.symlink_to(self.source/'now-playing.json')
        _,m=self.b.playback()
        with self.assertRaises(ValueError):self.b.roon.cover(m)
