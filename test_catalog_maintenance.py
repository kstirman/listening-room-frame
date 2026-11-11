import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
import zipfile
from music_catalog import Store
from catalog_maintenance import backup, audit

class IntakeTests(unittest.TestCase):
    def test_unresolved_survives_restart_and_keeps_editions_separate(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'catalog.db'; store=Store(path)
            base={'album':'Album','artist':'Artist','title':'Track','duration_seconds':123,
                  'resource':'http://secret.invalid/token?password=secret', 'password':'secret'}
            for edition in ('one','two'):
                store.encounter(dict(base,albumArtURI=f'https://static.qobuz.com/images/covers/ab/cd/{edition}_600.jpg'))
            store.encounter(dict(base,albumArtURI='https://static.qobuz.com/images/covers/ab/cd/one_600.jpg'))
            with Store(path).connect() as db:
                self.assertEqual(db.execute('SELECT count(*) FROM encountered_albums').fetchone()[0],2)
                self.assertEqual(db.execute('SELECT count(*) FROM encountered_tracks').fetchone()[0],2)
                records=db.execute('SELECT payload FROM encountered_tracks').fetchall()
                self.assertNotIn('secret',str(records))
                self.assertTrue(all(json.loads(r[0])['identity_status']=='candidate' for r in records))
            store.encounter({'album':'Favorite only','album_id':'three','source':'qobuz'})
            with store.connect() as db:
                self.assertEqual(db.execute('SELECT count(*) FROM encountered_albums').fetchone()[0],3)
                self.assertEqual(db.execute('SELECT count(*) FROM encountered_tracks').fetchone()[0],2)
            self.assertIsNone(Store(path).edition('one'))

    def test_metadata_backup_restores_without_configuration_or_credentials(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); store=Store(root/'live.db')
            store.encounter({'album':'Unknown','artist':'Artist','title':'Song'})
            with store.connect() as db:
                db.execute('INSERT INTO documents VALUES(?,?,?,?,?)',('overrides','/private/overrides.json','1',0,'{"tracks":{}}'))
            result=backup(store.path,root/'backup.zip')
            with zipfile.ZipFile(result['path']) as archive:
                archive.extract('music-catalog.sqlite3',root/'restore')
                self.assertIn('documents/overrides.json',archive.namelist())
                self.assertNotIn('config.json',archive.namelist())
            with sqlite3.connect(root/'restore/music-catalog.sqlite3') as db:
                self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0],'ok')
                self.assertEqual(db.execute('SELECT count(*) FROM encountered_albums').fetchone()[0],1)
            with self.assertRaises(FileExistsError): backup(store.path,root/'backup.zip')

    def test_audit_does_not_treat_release_date_as_recording_date(self):
        with tempfile.TemporaryDirectory() as tmp:
            store=Store(Path(tmp)/'catalog.db')
            rows={'1':{'title':'First','releaseDate':'1999-11-16','_catalog_track_id':'1'},
                  '2':{'title':'Second','releaseDate':'1999-11-16','_catalog_track_id':'2'}}
            with store.connect() as db:
                db.execute('INSERT INTO editions VALUES(?,?,?)',('edition',0,json.dumps(rows)))
                db.execute('INSERT INTO documents VALUES(?,?,?,?,?)',('recording_dates','/private/dates.json','1',0,
                    json.dumps({'qobuz/track/1':{'recording_year':'1962','recording_source':'https://example.org/session'}})))
            report=audit(store.path)
            self.assertEqual(report['streamed']['missing_recording_date'],1)
