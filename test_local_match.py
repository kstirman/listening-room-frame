import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, Mock
from bridge import Bridge
from local_match import Index

META={'title':'Original title','album':'Original album','artist':'Original artist','duration_seconds':120,'genre':'Classical','resource':'http://example.test/audirvana/audio_f_13.wav'}
TAGS={'title':'Original title','album':'Original album','artist':'Original artist','duration_seconds':120}


class LocalMatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.db=self.root/'.listening-room-metadata';self.db.mkdir()
        self.write(['one.flac'])

    def tearDown(self):self.tmp.cleanup()

    def write(self, names):
        fields={'work':'Reviewed work','composer':'Reviewed composer','movementName':'II. Slow','recordingDate':'1950','conductor':'Reviewed conductor'}
        for n in names:(self.root/n).touch()
        inventory={'albums':[{'tracks':[{'path':n,'title':META['title'],'tags':{k:[v] for k,v in TAGS.items() if k!='duration_seconds'}} for n in names]}]}
        (self.db/'inventory.json').write_text(json.dumps(inventory))
        (self.db/'enriched.json').write_text(json.dumps({'tracks':{n:{'fields':fields} for n in names}}))
        (self.db/'track-overrides.json').write_text(json.dumps({names[0]:{'fields':{'recordingDate':'1951','composition_group':'reviewed-performance','composition_index':2,'composition_count':4}}}))

    def test_reviewed_fields_overrides_and_existing_file_verified(self):
        with patch('local_match.local_composition.probe',return_value=TAGS) as probe:
            index=Index();match,status=index.lookup(META,self.root)
            self.assertEqual(status,'local_matched');self.assertEqual(match[1]['recordingDate'],'1951')
            self.assertEqual(match[1]['composition_index'],2)
            index.lookup(META,self.root);self.assertEqual(probe.call_count,1)
            (self.root/'one.flac').unlink();self.assertEqual(index.lookup(META,self.root)[1],'local_unmatched')

    def test_duplicates_alternate_lengths_and_stale_inventory(self):
        self.write(['one.flac','two.flac'])
        with patch('local_match.local_composition.probe',return_value=TAGS):
            self.assertEqual(Index().lookup(META,self.root)[1],'local_ambiguous')
        with patch('local_match.local_composition.probe',side_effect=lambda p:dict(TAGS,duration_seconds=120 if p.name=='one.flac' else 150)):
            self.assertEqual(Index().lookup(META,self.root)[1],'local_matched')
        with patch('local_match.local_composition.probe',return_value=dict(TAGS,title='Changed')):
            self.assertEqual(Index().lookup(META,self.root)[1],'local_unmatched')

    def test_metadata_update_invalidates_cached_corrections(self):
        index=Index()
        with patch('local_match.local_composition.probe',return_value=TAGS):
            index.lookup(META,self.root)
            (self.db/'track-overrides.json').write_text(json.dumps({'one.flac':{'fields':{'recordingDate':'1999'}}}))
            self.assertEqual(index.lookup(META,self.root)[0][1]['recordingDate'],'1999')

    def test_bridge_local_priority_and_composition_order(self):
        with tempfile.TemporaryDirectory() as state:
            b=Bridge({'library_root':str(self.root)},state)
            b.local_compositions.context=Mock(return_value={'index':1,'durations':[100,120],'label':'Part'})
            b.auto_catalog.lookup=Mock();b.album_page=Mock()
            with patch('local_match.local_composition.probe',return_value=TAGS):d=b.details(META)
            self.assertEqual(d['work'],'Reviewed work');self.assertEqual(d['recording_year'],'1951')
            self.assertEqual(d['conductor'],'Reviewed conductor');self.assertEqual(d['composition']['index'],1)
            self.assertEqual(d['catalog_status'],'local_matched')
            self.assertIn('one.flac',b.local_compositions.context.call_args.args[0])
            b.auto_catalog.lookup.assert_not_called();b.album_page.assert_not_called()

    def test_ambiguous_local_copy_does_not_fall_back_to_online(self):
        self.write(['one.flac','two.flac'])
        with tempfile.TemporaryDirectory() as state:
            b=Bridge({'library_root':str(self.root)},state);b.auto_catalog.lookup=Mock();b.album_page=Mock()
            with patch('local_match.local_composition.probe',return_value=TAGS):d=b.details(META)
            self.assertEqual(d['catalog_status'],'local_ambiguous')
            b.auto_catalog.lookup.assert_not_called();b.album_page.assert_not_called()

    def test_unreadable_candidate_and_missing_identity(self):
        with patch('local_match.local_composition.probe',side_effect=RuntimeError()):
            self.assertEqual(Index().lookup(META,self.root)[1],'local_unavailable')
        self.assertEqual(Index().lookup(dict(META,artist=''),self.root)[1],'local_insufficient')

    def test_reviewed_art_for_proxy_and_ambiguous_fallback(self):
        with tempfile.TemporaryDirectory() as state:
            b=Bridge({'library_root':str(self.root)},state)
            meta=dict(META,albumArtURI='http://example.test/fallback.jpg')
            b.config['local_art_overrides']={'local:/minimserver/*/AudirvanaLibrary:Original album':'http://example.test/reviewed.jpg'}
            with patch('local_match.local_composition.probe',return_value=TAGS):
                self.assertEqual(b.local_art_url(meta),'http://example.test/reviewed.jpg')
                self.write(['one.flac','two.flac'])
                self.assertEqual(b.local_art_url(meta),meta['albumArtURI'])

    def test_minim_escape_and_conflicting_art(self):
        with tempfile.TemporaryDirectory() as state:
            b=Bridge({},state)
            meta=dict(META,resource='http://local.invalid/music/An%20Album/CD1/one.flac',albumArtURI='http://example.test/fallback.jpg')
            b.config['local_art_overrides']={'local:/music/An*20Album:Original album':'http://example.test/reviewed.jpg'}
            self.assertEqual(b.local_art_url(meta),'http://example.test/reviewed.jpg')
            b.config['local_art_overrides']['local:/music/An%20Album/CD1:Original album']='http://example.test/conflict.jpg'
            self.assertEqual(b.local_art_url(meta),meta['albumArtURI'])

    def test_cached_proxy_cover_refresh_and_host_validation(self):
        import io
        from PIL import Image
        with tempfile.TemporaryDirectory() as state:
            b=Bridge({'library_root':str(self.root),'local_art_hosts':['example.test']},state)
            meta=dict(META,albumArtURI='http://example.test/fallback.jpg')
            key=b.album_key(meta)
            b.state.update(album=key,history=[{'key':key,'cover':'covers/old.jpg','title':'Old'}])
            b.config['local_art_overrides']={'local:/minimserver/*/AudirvanaLibrary:Original album':'http://example.test/reviewed.jpg'}
            data=io.BytesIO();Image.new('RGB',(32,32),'red').save(data,format='JPEG')
            b.session=Mock();b.session.get.return_value.content=data.getvalue()
            with patch('local_match.local_composition.probe',return_value=TAGS):
                b.card(meta,None)
                b.session.get.assert_called_once_with('http://example.test/reviewed.jpg',timeout=15)
                b.card(meta,None)
                self.assertEqual(b.session.get.call_count,1)
                b.config['local_art_overrides']['local:/minimserver/*/AudirvanaLibrary:Original album']='http://unapproved.test/art.jpg'
                with self.assertRaises(ValueError):b.cover(meta)
                self.assertEqual(b.session.get.call_count,1)

    def test_reviewed_performer_identity_without_fuzzy_matching(self):
        with patch('local_match.local_composition.probe',return_value=TAGS):
            index=Index()
            self.assertEqual(index.lookup(dict(META,artist='Reviewed conductor'),self.root)[1],'local_matched')
            for changes in ({'artist':'conductor'}, {'artist':'Reviewed composer'},
                            {'artist':'Reviewed conductor','album':'Another album'},
                            {'artist':'Reviewed conductor','duration_seconds':130}):
                self.assertEqual(index.lookup(dict(META,**changes),self.root)[1],'local_unmatched')
            self.write(['one.flac','two.flac'])
            self.assertEqual(index.lookup(dict(META,artist='Reviewed conductor'),self.root)[1],'local_ambiguous')

    def test_ffprobe_album_artist_alias(self):
        self.write(['one.flac'])
        p=self.db/'inventory.json'; data=json.loads(p.read_text())
        data['albums'][0]['tracks'][0]['tags']['albumartist']=['Album performer']
        p.write_text(json.dumps(data))
        with patch('local_match.local_composition.probe',return_value=dict(TAGS,album_artist='Album performer')):
            self.assertEqual(Index().lookup(dict(META,artist='Album performer'),self.root)[1],'local_matched')
