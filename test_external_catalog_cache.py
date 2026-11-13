import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock
from bridge import Bridge
from music_catalog import Store


class ExternalCatalogCacheTests(unittest.TestCase):
    def test_external_commits_refresh_details_without_source_request(self):
        for mode in ['delete', 'wal']:
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as d:
                b = Bridge({}, d)
                b.music_catalog.refresh = Mock()
                b.album_page = Mock(side_effect=AssertionError('no source request'))
                row={'title':'I. Allegro','work':'Violin Concerto in D Major, K. 211',
                     'composer':'Wolfgang Amadeus Mozart','album':'Fixture','albumArtist':'Ensemble',
                     'recordingDate':'1980','compositionDate':'1775','duration_seconds':100,
                     '_catalog_album_id':'fixture','_edition_verified':'fixture',
                     '_catalog_complete':True,'_catalog_expected_tracks':1}
                b.music_catalog.put_edition('fixture',{'1':row},None,None)
                writer=sqlite3.connect(b.music_catalog.path)
                try:
                    writer.execute('pragma journal_mode='+mode)
                    m={'album_id':'fixture','object_id':'qobuz/track/1','duration_seconds':100}
                    old=b.details(m)
                    self.assertEqual(old['recording_year'],'1980')
                    # Settle at most one invalidation due to the first observation.
                    settled=b.details(m)
                    self.assertIs(b.details(m),settled)
                    row['recordingDate']='1981';row['compositionDate']='1776'
                    writer.execute('update editions set payload=? where id=?',(json.dumps({'1':row}),'fixture'));writer.commit()
                    new=b.details(m)
                    self.assertEqual(new['recording_year'],'1981')
                    self.assertEqual(new['composition_year'],'1776')
                    self.assertNotEqual(new,old)
                    settled=b.details(m)
                    self.assertIs(b.details(m),settled)
                    b.album_page.assert_not_called()
                finally:writer.close()

    def test_identical_observation_does_not_change_database(self):
        with tempfile.TemporaryDirectory() as d:
            s=Store(Path(d)/'catalog.sqlite3')
            m={'album_id':'fixture'};details={'catalog_status':'catalog_id','recording_year':'1980'}
            s.remember(m,details);before=s.cache_revision()
            s.remember(m,details)
            self.assertEqual(s.cache_revision(),before)

    def test_cache_revision_is_read_only_and_sees_atomic_replacement(self):
        with tempfile.TemporaryDirectory() as d:
            s=Store(Path(d)/'catalog.sqlite3')
            before=s.cache_revision();self.assertFalse(s.path.exists())
            s.path.write_bytes(b'first');first=s.cache_revision();self.assertNotEqual(first,before)
            other=s.path.with_suffix('.replacement');other.write_bytes(b'first');other.replace(s.path)
            self.assertNotEqual(s.cache_revision(),first)

    def test_named_catalog_external_correction_reaches_bridge_without_network(self):
        from auto_catalog import album_rows
        from test_auto_catalog import META, page
        for mode in ['delete', 'wal']:
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as d:
                b=Bridge({}, d);b.music_catalog.refresh=Mock()
                b.auto_catalog.get=Mock(side_effect=AssertionError('no source request'))
                rows=album_rows(page(),META['album'],META['artist'])
                for row in rows.values():row['recordingDate']='1980'
                key=('example cycle','example choir')
                b.music_catalog.save(key,[rows])
                old=b.details(META)
                self.assertEqual(old['recording_year'],'1980')
                writer=sqlite3.connect(b.music_catalog.path)
                try:
                    writer.execute('pragma journal_mode='+mode)
                    payload=json.loads(writer.execute('select payload from catalogs').fetchone()[0])
                    for row in payload[0].values():row['recordingDate']='1981'
                    writer.execute('update catalogs set payload=?',(json.dumps(payload),));writer.commit()
                    new=b.details(META)
                    self.assertEqual(new['recording_year'],'1981')
                    b.auto_catalog.get.assert_not_called()
                finally:writer.close()

    def test_unrelated_writes_preserve_failed_cooldown_but_saved_insert_wins(self):
        import time
        from unittest.mock import patch
        from auto_catalog import Resolver, album_rows
        from test_auto_catalog import META, page
        with tempfile.TemporaryDirectory() as d:
            r=Resolver(Mock(),Path(d)/'catalog.sqlite3')
            key=('example cycle','example choir')
            r.cache[key]=(time.monotonic(),[],'unavailable')
            original=r.cache[key]
            with patch('auto_catalog.threading.Thread') as thread:
                self.assertEqual(r.lookup(META),(None,'unavailable'))
                for i in range(3):
                    r.disk.remember({'album_id':str(i)},{'recording_year':'1980'})
                    self.assertEqual(r.lookup(META),(None,'unavailable'))
                    self.assertIs(r.cache[key],original)
                rows=album_rows(page(),META['album'],META['artist'])
                r.disk.save(key,[rows])
                self.assertEqual(r.lookup(META)[1],'matched')
                thread.assert_not_called()
                r.get.assert_not_called()

    def test_stale_saved_revalidation_does_not_reschedule_or_extend_refresh(self):
        import time
        from unittest.mock import patch
        from auto_catalog import Resolver, album_rows
        from test_auto_catalog import META, page
        with tempfile.TemporaryDirectory() as d:
            r=Resolver(Mock(),Path(d)/'catalog.sqlite3')
            key=('example cycle','example choir')
            rows=album_rows(page(),META['album'],META['artist'])
            r.disk.save(key,[rows])
            self.assertEqual(r.lookup(META)[1],'matched')
            started=r.cache[key][0]
            with sqlite3.connect(r.disk.path) as db:
                db.execute('update catalogs set saved_at=0')
            with patch('auto_catalog.threading.Thread') as thread:
                self.assertEqual(r.lookup(META)[1],'matched')
                self.assertEqual(r.cache[key][0],started)
                thread.assert_not_called()
                with patch('auto_catalog.time.monotonic',return_value=started+1801):
                    self.assertEqual(r.lookup(META)[1],'matched')
                thread.assert_called_once()
                r.get.assert_not_called()
