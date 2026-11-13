import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from music_catalog import Store

class SparseEncounterTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'catalog.db';self.store=Store(self.path)
    def tearDown(self):self.tmp.cleanup()
    def rows(self, table):
        with Store(self.path).connect() as db:
            return db.execute('SELECT * FROM '+table).fetchall()
    def test_sparse_provider_ids_survive_restart_without_secrets(self):
        self.store.encounter({'album_id':'abc123','object_id':'qobuz/track/42','duration_seconds':249,'resource':'https://secret/?token=secret'})
        albums=self.rows('encountered_albums');tracks=self.rows('encountered_tracks')
        self.assertEqual(len(albums),1);self.assertEqual(len(tracks),1)
        self.assertEqual(json.loads(tracks[0][4])['object_id'],'qobuz/track/42')
        self.assertEqual(json.loads(albums[0][4])['identity_status'],'candidate')
        self.assertNotIn('secret',str(albums)+str(tracks))
        self.assertIsNone(Store(self.path).edition('abc123'))
    def test_sparse_observation_preserves_names_and_first_seen(self):
        rich={'album_id':'abc','object_id':'qobuz/track/42','album':'Album','artist':'Artist','title':'Song','duration_seconds':100}
        with patch('music_catalog.time.time',return_value=10):self.store.encounter(rich)
        with patch('music_catalog.time.time',return_value=20):self.store.encounter({'album_id':'abc','object_id':'qobuz/track/42','album':'','artist':'  ','title':'','duration_seconds':0})
        album=self.rows('encountered_albums')[0];track=self.rows('encountered_tracks')[0]
        self.assertEqual(album[2:4],(10,20));self.assertEqual(track[2:4],(10,20))
        self.assertEqual(json.loads(album[4])['album'],'Album')
        self.assertEqual(json.loads(track[4])['title'],'Song')
        self.assertEqual(json.loads(track[4])['artist'],'Artist')
        self.assertEqual(json.loads(track[4])['duration_seconds'],100)
    def test_later_metadata_enriches_same_exact_track(self):
        self.store.encounter({'album_id':'abc','object_id':'qobuz/track/42'})
        self.store.encounter({'album_id':'abc','object_id':'qobuz/track/42','album':'Named','title':'Named song'})
        self.assertEqual(len(self.rows('encountered_tracks')),1)
        self.assertEqual(json.loads(self.rows('encountered_tracks')[0][4])['title'],'Named song')
    def test_opaque_ids_and_duration_do_not_invent_tracks(self):
        for obj in ['https://host/secret','qobuz/track/42?token=secret','spotify/track/42','qobuz/track/notnumeric']:
            self.store.encounter({'album_id':'abc','object_id':obj,'duration_seconds':100})
        self.assertEqual(len(self.rows('encountered_albums')),1)
        self.assertEqual(self.rows('encountered_tracks'),[])
        self.assertNotIn('secret',str(self.rows('encountered_albums')))
    def test_cover_only_identity_and_rejected_unidentified_spotify(self):
        self.store.encounter({'albumArtURI':'https://static.qobuz.com/images/covers/ab/cd/cover123_600.jpg'})
        self.store.encounter({'duration_seconds':10})
        self.store.encounter({'album_id':'bad/secret','duration_seconds':10})
        self.store.encounter({'source':'spotify','album_id':'spotifyabc','album':'Named'})
        self.assertEqual(len(self.rows('encountered_albums')),1)
        self.assertEqual(self.rows('encountered_albums')[0][1],'cover123')
    def test_equal_titles_durations_keep_provider_tracks_and_editions_separate(self):
        for album,track in [('one','1'),('one','2'),('two','1')]:
            self.store.encounter({'album_id':album,'object_id':'qobuz/track/'+track,'title':'Same','duration_seconds':100})
        self.assertEqual(len(self.rows('encountered_tracks')),3)

if __name__=='__main__':unittest.main()
