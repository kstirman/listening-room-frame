import json
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import Mock, patch
from bridge import Bridge
from music_catalog import Store
from auto_catalog import Resolver, parsed_rows
from test_auto_catalog import META, page, search


class SharedCatalogTests(unittest.TestCase):
    def test_jplay_album_reused_by_audirvana_and_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            b = Bridge({},tmp); b.album_page = Mock(return_value=page())
            direct = dict(META,album_id='abc',object_id='qobuz/track/2')
            first = b.details(direct)
            self.assertEqual(first['composition']['index'],1)
            b2 = Bridge({},tmp); b2.album_page=Mock(side_effect=AssertionError('No page fetch'))
            b2.auto_catalog.get=Mock(side_effect=AssertionError('No discovery'))
            again=b2.details(META)
            self.assertEqual(again['composition'],first['composition'])
            self.assertEqual(again['composer'],first['composer'])
            b2.details(direct); b2.album_page.assert_not_called(); b2.auto_catalog.get.assert_not_called()

    def test_audirvana_album_reused_by_jplay(self):
        with tempfile.TemporaryDirectory() as tmp:
            b=Bridge({},tmp); b.auto_catalog.get=Mock(side_effect=[search(),page()])
            b.details(META)
            deadline=time.monotonic()+3
            while b.auto_catalog.busy and time.monotonic()<deadline:time.sleep(.01)
            b2=Bridge({},tmp); b2.album_page=Mock(side_effect=AssertionError('No page fetch'))
            self.assertEqual(b2.details(dict(META,album_id='abc',object_id='qobuz/track/1'))['composition']['index'],0)
            b2.album_page.assert_not_called()

    def test_all_genres_track_specific_roles_and_release_semantics(self):
        with tempfile.TemporaryDirectory() as tmp:
            html=page().replace('classique','Jazz').replace('Cycle: No. 2, Part','A Song').replace('Cycle: No. 1, Part','Another Song').replace('Example Composer, Composer','A Writer, Composer - A Player, Piano - A Producer, Producer')
            html=html.replace('"brand":', '"releaseDate":"2001-01-01","brand":')
            b=Bridge({},tmp); b.auto_catalog.get=Mock(side_effect=[search(),html])
            meta=dict(META,title='A Song',genre='Jazz'); b.details(meta)
            deadline=time.monotonic()+3
            while b.auto_catalog.busy and time.monotonic()<deadline:time.sleep(.01)
            d=b.details(meta)
            self.assertEqual(d['catalog_status'],'matched_track')
            self.assertNotIn('work',d);self.assertNotIn('recording_year',d)
            self.assertEqual(d['release_year'],'2001')
            self.assertEqual(d['liner_notes']['personnel'],[{'name':'A Player','role':'Piano'}])
            self.assertEqual(d['liner_notes']['producer'],'A Producer')

    def test_retention_more_than_256_and_idempotent_migration(self):
        with tempfile.TemporaryDirectory() as tmp:
            old=Store(Path(tmp)/'old.db'); row={'1':{'title':'Song','duration_seconds':120}}
            for i in range(260):old.save((str(i),'Artist'),[row])
            new=Store(Path(tmp)/'new.db');new.migrate(old.path);new.migrate(old.path)
            with new.connect() as db:self.assertEqual(db.execute('SELECT count(*) FROM catalogs').fetchone()[0],260)
            self.assertIsNotNone(new.load(('0','Artist')))
            self.assertEqual(new.path.stat().st_mode & 0o777,0o600)

    def test_two_explicit_editions_remain_ambiguous_without_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            store=Store(Path(tmp)/'catalog.db')
            for album_id in ('a','b'):store.put_edition(album_id,parsed_rows(page(),album_id),META['album'],META['artist'])
            resolver=Resolver(Mock(),store.path)
            self.assertEqual(resolver.lookup(META)[1],'ambiguous')
            self.assertIsNotNone(store.edition('a'));self.assertIsNotNone(store.edition('b'))

    def test_local_import_overrides_uniqueness_stale_file_and_incremental_probe(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'music';root.mkdir();meta=root/'.listening-room-metadata';meta.mkdir()
            p=root/'one.flac';p.touch()
            (meta/'enriched.json').write_text(json.dumps({'tracks':{'one.flac':{'fields':{'conductor':'Reviewed Person','recordingDate':'1950'}}}}))
            override=meta/'track-overrides.json';override.write_text(json.dumps({'one.flac':{'fields':{'recordingDate':'1951'}}}))
            tags={'title':'Song','album':'Album','artist':'Artist','duration_seconds':120}
            store=Store(Path(tmp)/'catalog.db');prefix='/music/'
            with patch('music_catalog.local_composition.probe',return_value=tags) as probe:
                self.assertEqual(store.sync_library(root)['saved'],1)
                store.sync_library(root);self.assertEqual(probe.call_count,1)
                match,status=store.match_local(dict(tags,artist='Reviewed Person'),root,prefix)
                self.assertEqual(status,'local_matched');self.assertEqual(match[1]['recordingDate'],'1951')
                override.write_text(json.dumps({'one.flac':{'fields':{'recordingDate':'1952'}}}))
                store.sync_library(root);self.assertEqual(probe.call_count,1)
                self.assertEqual(store.match_local(tags,root,prefix)[0][1]['recordingDate'],'1952')
                (root/'two.flac').touch();store.sync_library(root)
                self.assertEqual(store.match_local(tags,root,prefix)[1],'local_ambiguous')
                (root/'two.flac').unlink();p.write_bytes(b'changed')
                self.assertEqual(store.match_local(tags,root,prefix)[1],'local_unmatched')

    def test_reviewed_liner_wins_over_web_credits(self):
        with tempfile.TemporaryDirectory() as tmp:
            file=Path(tmp)/'liner.json'
            file.write_text(json.dumps({'albums':[{'album':META['album'],'artist':META['artist'],'review_status':'reviewed','source':'https://example.test/credits','tracks':[{'title':META['title'],'duration_seconds':180,'fields':{'producer':'Reviewed Producer'}}]}]}))
            b=Bridge({'liner_notes_file':str(file)},Path(tmp)/'state')
            b.album_page=Mock(return_value=page().replace('Example Composer, Composer','Web Producer, Producer'))
            d=b.details(dict(META,album_id='abc',object_id='qobuz/track/2'))
            self.assertEqual(d['liner_notes']['producer'],'Reviewed Producer')

    def test_recording_registry_shared_by_catalog_track_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            b=Bridge({},tmp)
            with b.music_catalog.connect() as db:
                db.execute('INSERT OR REPLACE INTO documents VALUES(?,?,?,?,?)',('recording_dates','fixture','1',0,json.dumps({'qobuz/track/2':{'recording_year':'1960','recording_source':'https://example.test/session'}})))
            b.music_catalog.put_edition('abc',parsed_rows(page(),'abc'),META['album'],META['artist'])
            self.assertEqual(b.details(META)['recording_year'],'1960')

    def test_corrupt_store_falls_back_without_losing_supplied_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            b=Bridge({'auto_catalog':False},tmp)
            b.music_catalog.path.write_text('corrupt')
            d=b.details(dict(META,composer='Known Composer',recordingDate='1961'))
            self.assertEqual(d['recording_year'],'1961')
            self.assertEqual(d['composer'],'Known Composer')

    def test_local_tag_aliases_preserve_existing_date_and_work_semantics(self):
        from music_catalog import tag_fields
        fields=tag_fields({'composition':'A Work','recording_year':'1960',
                           'composition_year':'1800','movement':'2','ensemble':'A Group'})
        self.assertEqual(fields['work'],'A Work')
        self.assertEqual(fields['recordingDate'],'1960')
        self.assertEqual(fields['compositionDate'],'1800')
        self.assertEqual(fields['movementNumber'],'2')
        self.assertEqual(fields['orchestra'],'A Group')

    def test_reviewed_mapping_reuses_catalog_without_fetch(self):
        with tempfile.TemporaryDirectory() as tmp:
            config={'reviewed_catalog_albums':[dict(album=META['album'],artist=META['artist'],qobuz_album_id='abc')]}
            first=Bridge(config,tmp);first.album_page=Mock(return_value=page())
            self.assertEqual(first.details(META)['composition']['index'],1)
            second=Bridge(config,tmp);second.album_page=Mock(side_effect=AssertionError('No fetch'))
            self.assertEqual(second.details(META)['composition']['index'],1)
            second.album_page.assert_not_called()

    def test_legacy_saved_raw_credits_enrich_without_refetch(self):
        with tempfile.TemporaryDirectory() as tmp:
            b=Bridge({},tmp)
            row={'title':'A Song','duration_seconds':180,'genre':'Jazz',
                 'credits':'A Writer, Composer - A Player, Piano'}
            b.music_catalog.save(('example cycle','example choir'),[{'2':row}])
            b.auto_catalog.get=Mock(side_effect=AssertionError('No fetch'))
            d=b.details(dict(META,title='A Song',genre='Jazz'))
            self.assertEqual(d['liner_notes']['songwriters'],'A Writer')
            self.assertEqual(d['liner_notes']['personnel'][0]['role'],'Piano')
            b.auto_catalog.get.assert_not_called()
