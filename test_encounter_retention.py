import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from bridge import Bridge
from music_catalog import Store


class EncounterRetentionTests(unittest.TestCase):
    def bridge(self, store):
        b = Bridge.__new__(Bridge)
        b.music_catalog = store
        return b

    def test_late_names_saved_without_playback_identity_change(self):
        with tempfile.TemporaryDirectory() as d:
            store = Store(Path(d)/'catalog.sqlite3')
            b = self.bridge(store)
            m = {'album_id': 'fixture', 'object_id': 'qobuz/track/42', 'duration_seconds': 80}
            b.observe_playback('PLAYING', m)
            b.observe_playback('PLAYING', dict(m, album='Album', artist='Artist', title='Track'))
            with store.connect() as db:
                album = json.loads(db.execute('select payload from encountered_albums').fetchone()[0])
                track = json.loads(db.execute('select payload from encountered_tracks').fetchone()[0])
                self.assertEqual(db.execute('select count(*) from encountered_tracks').fetchone()[0], 1)
            self.assertEqual(album['album'], 'Album')
            self.assertEqual(track['title'], 'Track')

    def test_failed_write_retries_after_backoff_then_stops(self):
        store = Mock(); store.encounter.side_effect = [None, True]
        b = self.bridge(store)
        m = {'album_id': 'fixture', 'object_id': 'qobuz/track/42'}
        for now in [100, 101, 129, 130, 131, 200]:
            with patch('bridge.time.monotonic', return_value=now):
                b.observe_playback('PLAYING', m)
        self.assertEqual(store.encounter.call_count, 2)

    def test_new_track_not_blocked_by_prior_failure(self):
        store = Mock(); store.encounter.side_effect = [None, True]
        b = self.bridge(store)
        with patch('bridge.time.monotonic', return_value=100):
            b.observe_playback('PLAYING', {'album_id':'a', 'object_id':'qobuz/track/1'})
            b.observe_playback('PLAYING', {'album_id':'a', 'object_id':'qobuz/track/2'})
        self.assertEqual(store.encounter.call_count, 2)

    def test_pause_does_not_write_and_resume_updates_seen(self):
        store = Mock(); store.encounter.return_value = True
        b = self.bridge(store); m = {'album_id':'a', 'object_id':'qobuz/track/1'}
        for status in ['PLAYING','PLAYING','PAUSED_PLAYBACK','PLAYING','PLAYING']:
            b.observe_playback(status,m)
        self.assertEqual(store.encounter.call_count, 2)

    def test_store_reports_failure_without_claiming_saved(self):
        with tempfile.TemporaryDirectory() as d:
            store = Store(Path(d)/'catalog.sqlite3')
            with patch.object(store,'connect',side_effect=sqlite3.OperationalError('locked')):
                self.assertIsNone(store.encounter({'album_id':'fixture'}))
            self.assertIs(store.encounter({'album_id':'fixture'}), True)
            self.assertIs(store.encounter({'source':'spotify'}), True)

    def test_missing_or_legacy_store_cannot_break_playback(self):
        for store in [None, object()]:
            b = self.bridge(store)
            b.observe_playback('PLAYING', {'album_id':'a', 'object_id':'qobuz/track/1'})
            self.assertEqual(b._last_playback_signature[0], 'PLAYING')
        # A legacy None return is ambiguous, not proof that storage succeeded.
        store = Mock(); store.encounter.return_value = None
        b = self.bridge(store)
        for now in [100,101,129,130]:
            with patch('bridge.time.monotonic',return_value=now):
                b.observe_playback('PLAYING', {'album_id':'a'})
        self.assertEqual(store.encounter.call_count,2)
        self.assertFalse(hasattr(b,'_last_encounter_saved'))

    def test_changed_artwork_edition_is_retained_even_with_stable_album_key(self):
        store = Mock(); store.encounter.return_value = True
        b = self.bridge(store)
        m = {'album':'Album', 'title':'Track', 'resource':'http://local.invalid/folder/track.flac'}
        for album in ['first','second','second']:
            b.observe_playback('PLAYING',dict(m,albumArtURI=
                'https://static.qobuz.com/images/covers/ab/cd/'+album+'_600.jpg'))
        self.assertEqual(store.encounter.call_count,2)

    def test_commit_failure_does_not_report_success(self):
        from contextlib import contextmanager
        @contextmanager
        def failed_commit():
            db = Mock(); db.execute.return_value.fetchone.return_value = None
            yield db
            raise sqlite3.OperationalError('commit unavailable')
        with tempfile.TemporaryDirectory() as d:
            store = Store(Path(d)/'catalog.sqlite3')
            with patch.object(store,'connect',failed_commit):
                self.assertIsNone(store.encounter({'album_id':'fixture'}))
