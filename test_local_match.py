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
