import copy
import json
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import Mock, patch
from bridge import Bridge
from persistent_metadata import Cache, Resolver, MusicBrainz, exact_release, jplay_records, mb_facts, discogs_records

ALBUM={'album_id':'fixture','album':'Example','artist':'Musician','expected_tracks':2,
 'releaseDate':'2020-01-01','tracks':{'71':{'title':'One','artist':'Musician','duration_seconds':101},
                                  '72':{'title':'Two','artist':'Musician','duration_seconds':202}}}
META={'album_id':'fixture','object_id':'qobuz/track/71','duration_seconds':101}
RID='12345678-1234-1234-1234-123456789abc'
RELEASE={'id':RID,'title':'Example','date':'2020-01-01','label-info':[{'label':{'name':'Label'}}],
 'relations':[{'url':{'resource':'https://www.qobuz.com/us-en/album/example/fixture'}}],
 'media':[{'tracks':[{'title':'One','length':101000,'recording':{'id':'recording-one','relations':[]}},
                     {'title':'Two','length':202000,'recording':{'id':'recording-two','relations':[]}}]}]}

class PermanentMetadataTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/'catalog.sqlite3';self.cache=Cache(self.path)
    def save(self):self.cache.write([('jplay','fixture',ALBUM)])
    def test_restart_and_decades_do_not_expire_or_request_metadata(self):
        self.save();r=Resolver(self.path,True,get=Mock(side_effect=AssertionError('offline')))
        identity=r.identity(ALBUM)
        self.cache.write([('musicbrainz-album',identity,{'fields':{'label':'Saved Label'},'status':'exact_edition'})])
        with sqlite3.connect(self.path) as db:
            db.execute('UPDATE metadata_records SET saved_at=0')
        restarted=Resolver(self.path,True,get=r.provider.get)
        with patch('persistent_metadata.time.time',return_value=100*365*86400),patch('persistent_metadata.threading.Thread') as thread:
            fields=restarted.lookup(META)
            self.assertEqual(fields['title'],'One');self.assertEqual(fields['label'],'Saved Label')
            thread.assert_not_called();r.provider.get.assert_not_called()
    def test_failed_and_partial_imports_never_erase_good_facts_and_versions_survive(self):
        self.save();self.cache.write([('jplay','fixture',{'album':'Wrong','artist':'','tracks':{'71':{'title':''},'73':{'title':'Three'}}})])
        saved=self.cache.get('jplay','fixture')
        self.assertEqual(saved['album'],'Example');self.assertEqual(saved['tracks']['71']['title'],'One')
        self.assertEqual(saved['tracks']['73']['title'],'Three')
        with sqlite3.connect(self.path) as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM metadata_versions').fetchone()[0],2)
    def test_exact_track_ids_never_duration_only(self):
        self.save()
        self.assertEqual(self.cache.jplay(META)[0]['title'],'One')
        for m in [dict(META,object_id='qobuz/track/999'),dict(META,object_id='71'),dict(META,duration_seconds=109),dict(META,title='Another')]:
            fields,_=self.cache.jplay(m);self.assertNotIn('title',fields);self.assertEqual(fields['album'],'Example')
        self.assertEqual(self.cache.jplay(dict(META,album_id='other'))[0],{})
    def test_musicbrainz_requires_exact_link_and_complete_correlated_tracks(self):
        self.assertTrue(exact_release(ALBUM,RELEASE))
        for change in ['no-link','other-id','partial','title','duration','duplicate']:
            r=copy.deepcopy(RELEASE)
            if change=='no-link':r['relations']=[]
            if change=='other-id':r['relations'][0]['url']['resource']='https://qobuz.com/album/other'
            if change=='partial':r['media'][0]['tracks'].pop()
            if change=='title':r['media'][0]['tracks'][0]['title']='Remix'
            if change=='duration':r['media'][0]['tracks'][0]['length']=120000
            if change=='duplicate':r['media'][0]['tracks'][1]=copy.deepcopy(r['media'][0]['tracks'][0])
            self.assertFalse(exact_release(ALBUM,r),change)
    def test_release_year_is_never_recording_or_composition_year(self):
        result=mb_facts(ALBUM,RELEASE)
        self.assertEqual(result['fields']['releaseDate'],'2020-01-01')
        self.assertNotIn('recordingDate',result['fields']);self.assertNotIn('compositionDate',result['fields'])
    def test_new_lookup_is_background_and_provider_failure_is_permanent_until_retry(self):
        self.save();r=Resolver(self.path,True)
        r.provider.discover=Mock(side_effect=RuntimeError('offline'))
        self.assertEqual(r.lookup(META)['title'],'One')
        for _ in range(100):
            if not r.busy:break
            time.sleep(.01)
        self.assertFalse(r.busy)
        restarted=Resolver(self.path,True)
        with patch('persistent_metadata.threading.Thread') as thread:
            self.assertEqual(restarted.lookup(META)['title'],'One');thread.assert_not_called()
        self.assertEqual(self.cache.get('jplay','fixture'),ALBUM)
    def test_api_response_reused_forever_and_throttled(self):
        response=Mock(content=b'{}');response.json.return_value={'releases':[]}
        get=Mock(return_value=response);provider=MusicBrainz(self.cache,get)
        provider.request('release',{'query':'first'})
        with patch('persistent_metadata.time.sleep') as sleep:
            provider.request('release',{'query':'second'});sleep.assert_called_once()
        restarted=MusicBrainz(self.cache,Mock(side_effect=AssertionError('offline')))
        self.assertEqual(restarted.request('release',{'query':'first'}),{'releases':[]})
    def test_candidates_are_saved_without_promoting_similarity_to_identity(self):
        provider=MusicBrainz(self.cache)
        candidate=copy.deepcopy(RELEASE);candidate['relations']=[]
        provider.request=Mock(side_effect=[{'releases':[{'id':RID,'title':'Example'}]},candidate])
        result=provider.discover(ALBUM)
        self.assertEqual(result['status'],'needs_review');self.assertEqual(result['fields'],{})
    def test_failed_request_is_not_written_as_success(self):
        response=Mock();response.raise_for_status.side_effect=RuntimeError('403')
        provider=MusicBrainz(self.cache,Mock(return_value=response))
        with self.assertRaises(RuntimeError):provider.request('release',{'query':'first'})
        self.assertFalse(self.path.exists())
    def test_jplay_import_strips_stream_urls_and_does_not_touch_other_tables(self):
        album=copy.deepcopy(ALBUM);album['token']='secret';album['tracks']['71']['trackURI']='private'
        records=jplay_records({'schema':1,'source':'JPLAY local music database','albums':[album]})
        self.assertNotIn('secret',json.dumps(records));self.assertNotIn('private',json.dumps(records))
        with sqlite3.connect(self.path) as db:
            db.execute('CREATE TABLE editions(id,payload)');db.execute("INSERT INTO editions VALUES('reviewed','keep')")
        self.cache.write(records);self.cache.write(records)
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute('SELECT * FROM editions').fetchall(),[('reviewed','keep')])
            self.assertEqual(db.execute('SELECT count(*) FROM metadata_versions').fetchone()[0],1)
            self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0],'ok')
    def test_bridge_uses_jplay_with_no_qobuz_or_musicbrainz_connection(self):
        b=Bridge({'qobuz_metadata_enabled':False},self.tmp.name)
        Cache(b.music_catalog.path).write([('jplay','fixture',ALBUM)])
        b.music_catalog.refresh=Mock();b.album_page=Mock(side_effect=AssertionError('no Qobuz'))
        result=b.details(META)
        self.assertEqual(result['display_album'],'Example');self.assertEqual(result['display_artist'],'Musician')
        self.assertEqual(result['display_track'],'One');b.album_page.assert_not_called()
        b.music_catalog.put_edition('fixture',{'71':{'title':'One','album':'Example','artist':'Musician','recordingDate':'1971',
              '_catalog_album_id':'fixture','_edition_verified':'fixture'}},'Example','Musician')
        self.assertEqual(b.details(META)['recording_year'],'1971')
    def test_discogs_local_cc0_import_is_selective(self):
        p=Path(self.tmp.name)/'releases.xml'
        p.write_text('<releases><release id="1"><title>Other</title></release><release id="2"><title>Example</title>'
            '<released>1990</released><notes>Excluded prose</notes><images><image uri="private"/></images>'
            '<tracklist><track><title>One</title><duration>1:41</duration></track></tracklist></release></releases>')
        records=list(discogs_records(p,{'2'}));self.assertEqual(len(records),1)
        self.assertEqual(records[0][2]['tracks'][0]['title'],'One')
        self.assertNotIn('Excluded',json.dumps(records));self.assertNotIn('private',json.dumps(records))

if __name__=='__main__':unittest.main()
