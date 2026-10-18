import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock
from PIL import Image
from photo_exclusions import Exclusions
from photo_stream import Index, Stream
from shared_album import media_row
import idle_photos


def key(n):return '%064x'%n


class ExclusionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.path=self.root/'deny.json'
        self.config={'photo_exclusions_file':str(self.path)}
        self.path.write_text(json.dumps({'schema':1,'content_keys':[key(100)],'media_keys':[key(1)]}))

    def tearDown(self):self.temp.cleanup()

    def test_cross_album_content_key_blocks_different_media_id(self):
        r=['album-a',['https://lh3.googleusercontent.com/test',80,60],0,'same-content',0,0]
        a=media_row(r);r[0]='album-b';b=media_row(r)
        self.assertNotEqual(a['key'],b['key']);self.assertEqual(a['content_key'],b['content_key'])
        self.path.write_text(json.dumps({'schema':1,'content_keys':[a['content_key']],'media_keys':[]}))
        self.assertFalse(Exclusions(self.config).allows(b))

    def test_missing_corrupt_and_unknown_metadata_fail_closed(self):
        valid={'key':key(2),'content_key':key(200)}
        self.assertTrue(Exclusions(self.config).allows(valid))
        self.assertFalse(Exclusions(self.config).allows({'key':key(2)}))
        for data in ('broken','[]','{"schema":1,"content_keys":["bad"],"media_keys":[]}'):
            self.path.write_text(data)
            self.assertFalse(Exclusions(self.config).allows(valid))
        self.path.unlink();self.assertFalse(Exclusions(self.config).allows(valid))
        self.assertTrue(Exclusions({}).allows({'key':key(2)}))

    def test_legacy_fallback_and_stream_manifest_cannot_bypass(self):
        (self.root/'images').mkdir()
        rows=[]
        for n,c in ((1,200),(2,100),(3,300),(4,None)):
            name='images/'+key(n)+'.jpg';Image.new('RGB',(10,10)).save(self.root/name)
            rows.append({'file':name,'content_key':key(c) if c else None})
        manifest=self.root/'ready.json';manifest.write_text(json.dumps({'photos':rows}))
        cfg=dict(self.config,idle_photos_enabled=True,idle_photos_file=str(manifest))
        self.assertEqual([r['key'] for r in idle_photos.load(cfg)],['images/'+key(3)+'.jpg'])
        stream=Stream(dict(cfg,photo_stream_dir=str(self.root),photo_stream_url_file='unused'))
        self.assertEqual([r['key'] for r in stream.available()],['images/'+key(3)+'.jpg'])

    def test_existing_database_migration_and_refresh_preserve_identity(self):
        dbpath=self.root/'index.sqlite3'
        db=sqlite3.connect(dbpath)
        db.execute("CREATE TABLE photos (key TEXT PRIMARY KEY,url TEXT NOT NULL,date TEXT,width INTEGER,height INTEGER,video INTEGER,active INTEGER DEFAULT 1,generation TEXT,extra TEXT DEFAULT '{}',failure_until REAL DEFAULT 0)")
        db.close()
        index=Index(dbpath)
        try:
            row={'key':key(2),'content_key':key(100),'url':'https://lh3.googleusercontent.com/a','date':'2000-01-01','width':80,'height':60,'video':False}
            index.upsert([row],'one');row.pop('content_key');index.upsert([row],'two')
            self.assertEqual(index.rows()[0]['content_key'],key(100))
            self.assertFalse(Exclusions(self.config).allows(index.rows()[0]))
        finally:index.close()

    def test_blocked_current_cache_is_purged_and_never_downloaded(self):
        stream=Stream(dict(self.config,photo_stream_dir=str(self.root),photo_stream_url_file='unused'),fetch=Mock(side_effect=AssertionError('download forbidden')))
        index=Index(self.root/'index.sqlite3')
        try:
            row={'key':key(2),'content_key':key(100),'url':'https://lh3.googleusercontent.com/a','date':'2000-01-01','width':80,'height':60,'video':False}
            index.upsert([row],'one')
            name='images/'+key(2)+'.jpg';Image.new('RGB',(10,10)).save(self.root/name)
            stream.manifest.write_text(json.dumps({'photos':[{'file':name,'content_key':key(100)}]}))
            stream.state={'photo_key':name}
            with self.assertRaises(ValueError):stream.prepare(index.rows()[0],index)
            self.assertEqual(stream.fill(index),(0,0))
            self.assertFalse((self.root/name).exists());self.assertEqual(stream.available(),[])
            stream.fetch.assert_not_called()
            index.upsert([row],'refresh');index.finish('refresh',100)
            self.assertEqual(stream.fill(index),(0,0))
        finally:index.close()
