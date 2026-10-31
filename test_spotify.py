import base64
import copy
import hashlib
import http.client
import io
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlparse

from PIL import Image
import requests

from bridge import Bridge
from setup_devices import validate_config
from setup_preview import PreviewBridge
from spotify_auth import authorization, Callback, CallbackServer
from spotify_source import (SpotifySource, SpotifyError, SCOPE, TOKEN_URL, PLAYBACK_URL,
                            save_tokens, track_metadata)

CLIENT = 'a'*32
TRACK = 'A'*22
ALBUM = 'B'*22
COVER = 'https://i.scdn.co/image/abc123'


def playing():
    return {'is_playing': True, 'currently_playing_type': 'track', 'item': {
        'type': 'track', 'id': TRACK, 'name': 'First movement', 'duration_ms': 123500,
        'artists': [{'name': 'Example artist'}], 'is_local': False,
        'album': {'id': ALBUM, 'name': 'Example album', 'release_date': '2024-05',
                  'artists': [{'name': 'Album artist'}],
                  'images': [{'url': COVER, 'width': 640},
                             {'url': 'https://i.scdn.co/image/small', 'width': 64}]}}}


def response(status=200, data=None, headers=None):
    r = Mock(status_code=status, headers=headers or {})
    r.json.return_value = data
    return r


class SourceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name)/'spotify-tokens.json'
        self.tokens = {'client_id': CLIENT, 'access_token': 'old-access', 'refresh_token': 'original-refresh',
                       'scope': SCOPE, 'expires_at': time.time()+3600}
        save_tokens(self.path, self.tokens)
        self.now = 100
        self.session = Mock()
        self.source = SpotifySource(str(self.path), self.session, lambda: self.now)
        self.session.get.return_value = response(data=playing())

    def tearDown(self):
        self.tmp.cleanup()

    def advance(self, seconds=6):
        self.now += seconds

    def refreshed(self, **changes):
        return {'access_token': 'new-access', 'token_type': 'Bearer', 'expires_in': 3600,
                'scope': SCOPE, **changes}

    def test_reads_only_current_playback_and_preserves_provider_identity(self):
        status, m = self.source.playback()
        self.assertEqual(status, 'PLAYING')
        self.assertEqual(m['source_album_key'], 'spotify:album:'+ALBUM)
        self.assertEqual(m['object_id'], 'spotify:track:'+TRACK)
        self.assertEqual(m['source_url'], 'https://open.spotify.com/track/'+TRACK)
        self.assertEqual(m['spotify_cover'], COVER)
        self.assertEqual(m['duration_seconds'], 123.5)
        self.assertEqual(m['releaseDate'], '2024-05')
        self.assertNotIn('album_id', m)
        self.assertNotIn('recordingDate', m)
        self.session.get.assert_called_once_with(PLAYBACK_URL, headers={'Authorization':'Bearer old-access'}, timeout=10, allow_redirects=False)
        self.session.post.assert_not_called()
        self.assertEqual(self.source.playback(), (status, m))
        self.assertEqual(self.session.get.call_count, 1)

    def test_expired_access_refreshes_and_preserves_unrotated_refresh_token(self):
        self.tokens['expires_at'] = 0;save_tokens(self.path, self.tokens)
        self.session.post.return_value = response(data=self.refreshed())
        self.assertEqual(self.source.playback()[0], 'PLAYING')
        saved = json.loads(self.path.read_text())
        self.assertEqual(saved['refresh_token'], 'original-refresh')
        self.assertEqual(saved['access_token'], 'new-access')
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        self.session.post.assert_called_once_with(TOKEN_URL, data={'grant_type':'refresh_token','client_id':CLIENT,'refresh_token':'original-refresh'}, timeout=10, allow_redirects=False)

    def test_401_refreshes_once_and_persists_rotated_token(self):
        self.session.get.side_effect = [response(401), response(data=playing())]
        self.session.post.return_value = response(data=self.refreshed(refresh_token='rotated'))
        self.assertEqual(self.source.playback()[0], 'PLAYING')
        self.assertEqual(self.session.get.call_count, 2)
        self.assertEqual(json.loads(self.path.read_text())['refresh_token'], 'rotated')

    def test_repeated_401_does_not_loop(self):
        self.session.get.return_value = response(401)
        self.session.post.return_value = response(data=self.refreshed())
        self.assertEqual(self.source.playback(), ('STOPPED', {}))
        self.assertIn('reconnect', self.source.error)
        self.assertEqual(self.session.get.call_count, 2)
        self.source.playback();self.assertEqual(self.session.get.call_count, 2)

    def test_revoked_refresh_is_discarded_and_does_not_repeat(self):
        self.tokens['expires_at'] = 0;save_tokens(self.path, self.tokens)
        self.session.post.return_value = response(400, {'error':'invalid_grant'})
        self.assertEqual(self.source.playback(), ('STOPPED', {}))
        self.assertNotIn('refresh_token', json.loads(self.path.read_text()))
        self.advance(65);self.source.playback()
        self.assertEqual(self.session.post.call_count, 1)
        save_tokens(self.path, {**self.tokens, 'expires_at':time.time()+3600})
        self.advance(65)
        self.assertEqual(self.source.playback()[0], 'PLAYING')

    def test_rate_limit_respected_and_stale_track_expires(self):
        self.source.playback();self.advance()
        self.session.get.return_value = response(429, headers={'Retry-After':'3600'})
        self.assertEqual(self.source.playback()[0], 'PLAYING')
        self.advance(31)
        self.assertEqual(self.source.playback(), ('STOPPED', {}))
        self.advance(3000);self.source.playback()
        self.assertEqual(self.session.get.call_count, 2)
        self.advance(600);self.session.get.return_value = response(data=playing())
        self.assertEqual(self.source.playback()[0], 'PLAYING')

    def test_token_rate_limit_does_not_destroy_grant(self):
        self.tokens['expires_at'] = 0;save_tokens(self.path, self.tokens)
        self.session.post.return_value = response(429, headers={'Retry-After':'80'})
        self.assertEqual(self.source.playback(), ('STOPPED', {}))
        self.advance(70);self.source.playback()
        self.assertEqual(self.session.post.call_count, 1)
        self.assertEqual(json.loads(self.path.read_text()), self.tokens)

    def test_absent_pause_ad_podcast_local_and_missing_art(self):
        self.source.playback();self.advance()
        self.session.get.return_value = response(204)
        self.assertEqual(self.source.playback(), ('STOPPED', {}))
        for edit, expected in [({'is_playing':False},'PAUSED_PLAYBACK'),
                               ({'currently_playing_type':'ad'},'STOPPED'),
                               ({'currently_playing_type':'episode'},'STOPPED'),
                               ({'item':None},'STOPPED')]:
            self.assertEqual(track_metadata({**playing(),**edit}), (expected, {}))
        for change in ({'is_local':True},{'id':None},{'album':None}):
            data=playing();data['item'].update(change)
            self.assertEqual(track_metadata(data),('PLAYING',{}))
        data=playing();data['item']['album']['images']=[]
        self.assertEqual(track_metadata(data),('PLAYING',{}))

    def test_denied_and_network_errors_do_not_leak_tokens(self):
        self.session.get.side_effect=requests.ConnectionError('secret-access-token in URL')
        with self.assertLogs('frame-art',level='WARNING') as logs:
            self.assertEqual(self.source.playback(),('STOPPED',{}))
        self.assertNotIn('secret-access-token',str(logs.output))
        self.advance(31);self.session.get.side_effect=None;self.session.get.return_value=response(403)
        self.source.playback();self.assertIn('allowlist',self.source.error)

    def test_bad_token_and_missing_scope_never_reach_network(self):
        for content in ('[]','{','{}',json.dumps({**self.tokens,'scope':'user-read-email'})):
            self.path.write_text(content);self.advance(61)
            self.assertEqual(self.source.playback(),('STOPPED',{}))
        self.session.get.assert_not_called();self.session.post.assert_not_called()

    def test_cover_cannot_forward_credentials_redirect_or_fetch_arbitrary_host(self):
        for url in ('file:///secret','https://evil.example/image/abc','https://i.scdn.co.evil/image/abc',
                    'https://user:secret@i.scdn.co/image/abc','http://i.scdn.co/image/abc','https://i.scdn.co:444/image/abc',
                    'https://i.scdn.co/image/abc?token=secret'):
            with self.assertRaises(ValueError):self.source.cover({'spotify_cover':url})
        r=response();r.__enter__=Mock(return_value=r);r.__exit__=Mock(return_value=False)
        r.iter_content.return_value=[b'cover']
        with patch('spotify_source.requests.get',return_value=r) as get:
            self.assertEqual(self.source.cover({'spotify_cover':COVER}),b'cover')
            get.assert_called_once_with(COVER,timeout=10,stream=True,allow_redirects=False)
        r.status_code=302
        with patch('spotify_source.requests.get',return_value=r):
            with self.assertRaises(ValueError):self.source.cover({'spotify_cover':COVER})
        r.status_code=200;r.iter_content.return_value=[b'x'*20_000_001]
        with patch('spotify_source.requests.get',return_value=r):
            with self.assertRaises(ValueError):self.source.cover({'spotify_cover':COVER})


class AuthorizationTests(unittest.TestCase):
    def test_pkce_and_minimal_scope(self):
        url, verifier, state=authorization(CLIENT,'http://127.0.0.1:8888/callback')
        query=parse_qs(urlparse(url).query)
        self.assertEqual(query['scope'],[SCOPE])
        self.assertEqual(query['state'],[state])
        self.assertEqual(query['code_challenge_method'],['S256'])
        expected=base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')
        self.assertEqual(query['code_challenge'],[expected])
        self.assertNotIn(verifier,url)

    def test_callback_requires_state_and_host_and_never_logs_code(self):
        with CallbackServer(('127.0.0.1',0),Callback) as server:
            port=server.server_port;server.expected_host=f'127.0.0.1:{port}'
            server.oauth_state='test-state';server.result=None
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            try:
                for path,host,code in [('/callback?code=secret&state=wrong',server.expected_host,400),
                                      ('/callback?code=secret&state=test-state','other.example',400),
                                      ('/callback?code=secret&state=test-state',server.expected_host,200)]:
                    connection=http.client.HTTPConnection('127.0.0.1',port,timeout=5)
                    connection.request('GET',path,headers={'Host':host})
                    reply=connection.getresponse();self.assertEqual(reply.status,code)
                    self.assertNotIn(b'secret',reply.read());connection.close()
                    if code==400:self.assertIsNone(server.result)
                self.assertEqual(server.result,{'code':'secret'})
            finally:server.shutdown();thread.join()

    def test_token_file_symlink_rejected(self):
        with tempfile.TemporaryDirectory() as t:
            target=Path(t)/'target';target.write_text('keep')
            path=Path(t)/'link';path.symlink_to(target)
            with self.assertRaises(SpotifyError):save_tokens(path,{'access_token':'secret'})
            self.assertEqual(target.read_text(),'keep')


class IntegrationTests(unittest.TestCase):
    def test_display_refresh_restore_and_no_cross_catalog_or_collage(self):
        with tempfile.TemporaryDirectory() as t,patch('bridge.SpotifySource') as source_class:
            source=source_class.return_value
            data=playing();source.playback.side_effect=lambda:track_metadata(data)
            cover=io.BytesIO();Image.new('RGB',(100,100),'navy').save(cover,format='JPEG')
            source.cover.return_value=cover.getvalue()
            b=Bridge({'source':'spotify','spotify_token_file':'/private/example','idle_seconds':0},Path(t))
            b.soap=Mock(side_effect=AssertionError('UPnP called'));b.session.get=Mock(side_effect=AssertionError('Qobuz called'))
            b.music_catalog=Mock();b.auto_catalog=Mock()
            tv=Mock();current=['ORIGINAL'];tv.get_artmode.return_value='on'
            tv.get_current.side_effect=lambda:{'content_id':current[0]}
            tv.select_image.side_effect=lambda value:current.__setitem__(0,value)
            tv.upload.side_effect=['FIRST','SECOND'];b.television=Mock(return_value=tv)
            b.next_collage=0;b.step();self.assertEqual(current[0],'FIRST')
            data['item']['name']='Second movement';data['item']['id']='C'*22
            b.step();self.assertEqual(current[0],'SECOND')
            source.cover.assert_called_once();self.assertEqual(b.state['history'],[])
            b.music_catalog.remember.assert_not_called();b.music_catalog.refresh.assert_not_called()
            b.auto_catalog.lookup.assert_not_called();b.session.get.assert_not_called();b.soap.assert_not_called()
            with Image.open(Path(t)/'current-cover.jpg') as image:self.assertEqual(image.size,(3840,2160))
            data['is_playing']=False;b.step();self.assertEqual(current[0],'ORIGINAL')

    def test_switching_from_existing_collage_selects_new_spotify_card(self):
        with tempfile.TemporaryDirectory() as t,patch('bridge.SpotifySource'):
            b=Bridge({'source':'spotify','spotify_token_file':'/private/example'},Path(t))
            b.state.update(view='collage',album_image='NEW_SPOTIFY')
            tv=Mock();tv.get_current.return_value={'content_id':'NEW_SPOTIFY'}
            b.rotate(tv,1)
            tv.select_image.assert_called_once_with('NEW_SPOTIFY')
            self.assertEqual(b.state['view'],'album');tv.upload.assert_not_called()

    def test_preview_link_is_in_manifest(self):
        with tempfile.TemporaryDirectory() as t,patch('bridge.SpotifySource'):
            b=PreviewBridge({'source':'spotify','spotify_token_file':'/private/example'},Path(t)/'display')
            cover=io.BytesIO();Image.new('RGB',(100,100),'navy').save(cover,format='JPEG');b.spotify.cover.return_value=cover.getvalue()
            _,metadata=track_metadata(playing());b.card(metadata,b.details(metadata))
            manifest=json.loads((Path(t)/'presentation.json').read_text())
            self.assertEqual(manifest['source'],'spotify')
            self.assertEqual(manifest['source_url'],'https://open.spotify.com/track/'+TRACK)
            self.assertEqual(manifest['status'],'prepared')

    def test_setup_persists_only_token_path(self):
        with patch('setup_devices.local_host',side_effect=lambda value:value):
            data=validate_config({'source':'spotify','tv':'frame.local','tv_model':'TEST',
                'spotify_token_file':'~/private/spotify-tokens.json','access_token':'never-save'})
            self.assertEqual(data['poll_seconds'],5)
            self.assertNotIn('access_token',data)
            for value in ('relative/path',None,'/bad\x00path'):
                with self.assertRaises(ValueError):validate_config({**data,'spotify_token_file':value})


if __name__ == '__main__':
    unittest.main()
