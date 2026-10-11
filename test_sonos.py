import io
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from PIL import Image
from bridge import Bridge
from sonos_source import SonosSource


class SonosTests(unittest.TestCase):
    def setUp(self):
        self.player = Mock(ip_address='192.0.2.1')
        self.player.get_current_transport_info.return_value = {'current_transport_state':'PLAYING'}
        self.info = {'title':'I. Allegro','artist':'Example Orchestra','album':'Symphonies',
                     'album_art':'/cover.jpg','uri':'test:track1'}
        self.player.get_current_track_info.side_effect = lambda: dict(self.info)
        self.room = Mock(uid='TEST_ROOM', group=SimpleNamespace(coordinator=self.player))
        self.soco = Mock(config=SimpleNamespace(), discover=Mock(return_value={self.room}))
        self.source = SonosSource('TEST_ROOM', self.soco)

    def test_selected_room_reads_coordinator_and_follows_group_change(self):
        status, first = self.source.playback()
        self.assertEqual(status, 'PLAYING')
        self.room.get_current_track_info.assert_not_called()
        second = Mock(ip_address='192.0.2.2')
        second.get_current_transport_info.return_value = {'current_transport_state':'PAUSED_PLAYBACK'}
        self.room.group = SimpleNamespace(coordinator=second)
        self.assertEqual(self.source.playback(), ('PAUSED_PLAYBACK', {}))
        second.get_current_transport_info.assert_called_once()

    def test_track_changes_keep_album_and_refresh_lines(self):
        _, first = self.source.playback()
        self.info.update(title='II. Andante', uri='test:track2')
        _, second = self.source.playback()
        self.assertEqual(first['source_album_key'], second['source_album_key'])
        self.assertNotEqual(first['object_id'], second['object_id'])
        self.assertEqual(second['sonos_lines'][0], 'II. Andante')

    def test_missing_room_does_not_choose_another_and_discovery_backoff(self):
        self.room.uid = 'OTHER'
        self.assertEqual(self.source.playback(), ('STOPPED', {}))
        self.assertEqual(self.source.playback(), ('STOPPED', {}))
        self.soco.discover.assert_called_once()

    def test_disconnect_stops_and_recovers(self):
        self.source.playback()
        self.player.get_current_transport_info.side_effect = OSError('private-url')
        with self.assertLogs(level='WARNING') as logs:
            self.assertEqual(self.source.playback(), ('STOPPED', {}))
        self.assertNotIn('private-url', str(logs.output))
        self.player.get_current_transport_info.side_effect = None
        self.source.discovered_at -= 31
        self.assertEqual(self.source.playback()[0], 'PLAYING')

    def test_missing_art(self):
        self.info['album_art'] = ''
        self.assertEqual(self.source.playback(), ('PLAYING', {}))

    def test_radio_art_fallback(self):
        self.info['album_art'] = ''
        self.info['metadata'] = '<item xmlns:u="urn:schemas-upnp-org:metadata-1-0/upnp/"><u:albumArtURI>/radio.jpg</u:albumArtURI></item>'
        self.assertEqual(self.source.playback()[1]['sonos_cover'], 'http://192.0.2.1:1400/radio.jpg')
        self.info['metadata'] = '<broken'
        self.assertEqual(self.source.playback(), ('PLAYING', {}))

    def test_cover_url_validation_and_limit(self):
        for url in ('file:///secret', 'http://user:pass@example.com/art', 'ftp://example.com/art'):
            with self.assertRaises(ValueError):
                self.source.cover({'sonos_cover':url})
        response = Mock(is_redirect=False)
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.iter_content.return_value = [b'x'*20_000_001]
        with patch('sonos_source.requests.get', return_value=response):
            with self.assertRaises(ValueError):
                self.source.cover({'sonos_cover':'https://example.com/art'})
        response.iter_content.return_value = [b'image']
        with patch('sonos_source.requests.get', return_value=response):
            self.assertEqual(self.source.cover({'sonos_cover':'https://example.com/art'}), b'image')

    def test_redirect_revalidates_target_and_bounds_loops(self):
        response = Mock(is_redirect=True, headers={'Location':'file:///secret'})
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        with patch('sonos_source.requests.get', return_value=response) as get:
            with self.assertRaises(ValueError):
                self.source.cover({'sonos_cover':'https://example.com/art'})
            self.assertEqual(get.call_count, 1)
        response.headers['Location'] = '/loop'
        with patch('sonos_source.requests.get', return_value=response) as get:
            with self.assertRaises(ValueError):
                self.source.cover({'sonos_cover':'https://example.com/art'})
            self.assertEqual(get.call_count, 5)

    def test_tv_track_refresh_and_restore_without_other_sources(self):
        with tempfile.TemporaryDirectory() as directory, patch('bridge.SonosSource', return_value=self.source):
            b = Bridge({'source':'sonos','sonos_uid':'TEST_ROOM','idle_seconds':0}, Path(directory))
            b.soap = Mock(side_effect=AssertionError('UPnP called'))
            b.session.get = Mock(side_effect=AssertionError('Catalogue called'))
            image = io.BytesIO()
            Image.new('RGB', (100,100), 'navy').save(image, format='JPEG')
            self.source.cover = Mock(return_value=image.getvalue())
            tv = Mock()
            current = ['ORIGINAL']
            tv.get_artmode.return_value = 'on'
            tv.get_current.side_effect = lambda: {'content_id':current[0]}
            tv.select_image.side_effect = lambda value: current.__setitem__(0,value)
            tv.upload.side_effect = ['CARD1','CARD2']
            b.television = Mock(return_value=tv)
            b.step()
            self.assertEqual(current[0], 'CARD1')
            self.info.update(title='II. Andante', uri='test:track2')
            b.step()
            self.assertEqual(current[0], 'CARD2')
            self.assertEqual(len(b.state['history']), 1)
            self.info['album_art'] = ''
            b.step()
            self.assertEqual(current[0], 'ORIGINAL')
            b.soap.assert_not_called()
            b.session.get.assert_not_called()
