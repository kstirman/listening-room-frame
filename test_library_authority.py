import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from bridge import Bridge
from library_authority import adopt, edit_document, patch_review
from music_catalog import Store


class LibraryAuthorityTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name)
        self.root = self.base/'music'
        self.meta = self.root/'.listening-room-metadata'
        self.meta.mkdir(parents=True)
        self.track = self.root/'Disc 1.flac'
        self.track.write_bytes(b'fixture')
        self.tags = {'genre':'Classical','title':'Prelude','artist':'Ensemble','album':'Original Box','album_artist':'Orchestra',
                     'track':'01/14','disc':'1/2','discsubtitle':'Act I','duration_seconds':143,
                     'composer':'Embedded Composer','compositiondate':'1848','date':'1999'}
        self.resource = 'http://local.invalid/music/Disc%201.flac'
        self.store = Store(self.base/'state'/'music-catalog.sqlite3')
        self.reviewed = {'tracks':{self.track.name:{'fields':{'album':'Proposed box','composer':'Proposal',
                    'compositionDate':'1849','composition_source':'A source for 1849'},
                    'provenance':{'album':'Booklet'}}}}
        self.overrides = {self.track.name:{'fields':{'discnumber':'9','composer':'Protected composer',
                                                      'recordingDate':'c. 1950'}}}
        (self.meta/'enriched.json').write_text(json.dumps(self.reviewed))
        (self.meta/'track-overrides.json').write_text(json.dumps(self.overrides))
        with patch('local_composition.probe',return_value=self.tags):
            self.store.sync_library(self.root)
        with self.store.connect() as db:
            db.execute("UPDATE local_tracks SET payload=json_set(payload,'$.fields.performer','Session guest')")
        self.checkpoint = self.base/'before.sqlite3'
        self.snapshot = self.base/'generation.json'
        self.snapshot.write_text(json.dumps({'format':'listening-room-music/v2','scope':self.root.name,
            'coverage':'audio-and-companions','complete':True,'paths':1,'files':[
                {'path':str(self.track.relative_to(self.base)), 'bytes':self.track.stat().st_size,
                 'sha256':hashlib.sha256(self.track.read_bytes()).hexdigest()}]}))
        self.snapshot_sha = hashlib.sha256(self.snapshot.read_bytes()).hexdigest()

    def adopt(self):
        return adopt(self.store, self.root, self.checkpoint,self.snapshot,self.snapshot_sha)

    def test_adopt_preserves_raw_protected_research_and_portable_dates(self):
        self.assertEqual(self.adopt()['changed'],1)
        result = self.store.portable_status(self.resource,self.root,'/music/')
        self.assertEqual(result['observed_tags']['discNumber'],'1')
        self.assertEqual(result['observed_tags']['discTotal'],'2')
        self.assertEqual(result['observed_tags']['trackTotal'],'14')
        self.assertEqual(result['observed_tags']['discSubtitle'],'Act I')
        self.assertEqual(result['reviewed_desires']['recordingDate']['status'],'proposed')
        self.assertEqual(result['research']['performer'],'Session guest')
        fields,_ = self.store.local(self.resource,self.root,'/music/')
        self.assertEqual(fields['album'],'Original Box')
        self.assertEqual(fields['composer'],'Protected composer')
        self.assertEqual(fields['compositionDate'],'1848')
        self.assertNotIn('composition_source',fields)
        self.assertEqual(fields['recordingDate'],'c. 1950')
        self.assertNotIn('originalReleaseDate',fields)
        self.assertEqual(fields['discNumber'],'1')
        self.assertEqual(self.adopt()['changed'],0)
        with sqlite3.connect(self.checkpoint) as db:
            data = json.loads(db.execute('SELECT payload FROM local_tracks').fetchone()[0])
            self.assertNotIn('authority_version', data)
            self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0],'ok')

    def test_mismatched_sidecar_rolls_back_before_checkpoint(self):
        (self.meta/'track-overrides.json').write_text('{}')
        with self.assertRaisesRegex(ValueError,'disagree'):
            self.adopt()
        self.assertFalse(self.store.adopted(self.root))
        self.assertFalse(self.checkpoint.exists())

    def test_unindexed_sidecar_is_retained_unbound_in_catalog_document(self):
        self.reviewed['tracks']['retired.flac'] = {'fields':{'work':'Unbound'}}
        (self.meta/'enriched.json').write_text(json.dumps(self.reviewed))
        self.store.sync_library(self.root)
        self.assertEqual(self.adopt()['unbound_evidence']['reviewed'],1)
        self.assertIn('retired.flac',self.store.document('enriched')['tracks'])
        self.assertIsNone(self.store.local('http://example.test/music/retired.flac',self.root,'/music/'))

    def test_cache_writer_preserved_and_legacy_sidecar_no_longer_changes_results(self):
        self.adopt()
        (self.meta/'enriched.json').write_text('{corrupt')
        self.store.sync_library(self.root)
        self.assertEqual(self.store.local(self.resource,self.root,'/music/')[0]['performer'],'Session guest')
        self.assertNotEqual(self.store.match_local(dict(self.tags,album='Proposed box'),self.root,'/music/')[1],'local_matched')
        self.assertEqual(self.store.match_local(self.tags,self.root,'/music/')[1],'local_matched')
        self.track.write_bytes(b'new recording at old path')
        self.assertEqual(self.store.sync_library(self.root)['identity_pending'],1)
        self.assertEqual(self.store.portable_status(self.resource,self.root,'/music/')['identity_status'],'verification_required')
        self.assertIsNone(self.store.local(self.resource,self.root,'/music/'))

    def test_restored_identical_bytes_rebind_and_differing_read_blocks_adoption(self):
        damaged = self.snapshot.read_text().replace(hashlib.sha256(self.track.read_bytes()).hexdigest(),'0'*64)
        bad = self.base/'bad.json'
        bad.write_text(damaged)
        with self.assertRaisesRegex(ValueError,'differs from recovery object'):
            adopt(self.store,self.root,self.checkpoint,bad,hashlib.sha256(bad.read_bytes()).hexdigest())
        self.assertFalse(self.checkpoint.exists())
        self.adopt()
        self.track.unlink()
        self.track.write_bytes(b'fixture')
        with patch('local_composition.probe',return_value=self.tags):
            self.assertEqual(self.store.sync_library(self.root)['identity_pending'],0)
        self.assertEqual(self.store.local(self.resource,self.root,'/music/')[0]['composer'],'Protected composer')

    def test_guarded_record_level_undo_and_protected_fields(self):
        self.adopt()
        entry = {'fields':{'compositionDate':'1860','conductor':'New credit','composer':'Wrong'},
                 'provenance':dict.fromkeys(('compositionDate','conductor','composer'),'Booklet p. 8')}
        proposal = {'tracks':{self.track.name:entry}}
        first = patch_review(self.store,self.root,proposal)
        self.assertEqual(first['changes'],1)
        self.assertEqual(patch_review(self.store,self.root,proposal)['changes'],0)
        self.assertEqual(self.store.local(self.resource,self.root,'/music/')[0]['conductor'],'New credit')
        self.assertEqual(patch_review(self.store,self.root,None,first['batch'])['changes'],1)
        self.assertNotIn('conductor',self.store.local(self.resource,self.root,'/music/')[0])

    def test_art_and_context_from_same_catalog_with_guarded_record_undo(self):
        context = self.base/'context.json'
        context.write_text(json.dumps({'albums':[{'album':'Record','source_album_key':'local:r'}]}))
        config = {'library_root':str(self.root),'music_context_file':str(context),
                  'local_art_overrides':{'local:fixture':'https://example.test/cover'}}
        config_path = self.base/'config.json'
        config_path.write_text(json.dumps(config))
        self.store.sync_documents(config,self.base)
        adopt(self.store,self.root,self.checkpoint,self.snapshot,self.snapshot_sha,config_path)
        before = self.store.document('music_context')['albums'][0]
        after = dict(before,artist='Artist')
        edit = {'kind':'music_context','before':before,'after':after,'source':'Reviewed booklet'}
        change = edit_document(self.store,self.root,edit)
        context.write_text('{"albums":[]}')
        self.store.sync_documents(config,self.base)
        self.assertEqual(self.store.document('music_context')['albums'],[after])
        self.assertEqual(self.store.art_overrides(self.root,{}),config['local_art_overrides'])
        edit_document(self.store,self.root,undo=change['batch'])
        self.assertEqual(self.store.document('music_context')['albums'],[before])

    def test_exact_edition_receipt_selects_only_released_paths(self):
        self.adopt()
        with self.store.connect() as db:
            db.execute('CREATE TABLE intake_publications(edition,destination,state,manifest)')
            for edition in ('opera-a','opera-b'):
                dest=self.root/edition
                dest.mkdir()
                tracks=[]
                for n in range(1,3):
                    track=dest/f'{n}.flac'
                    track.write_bytes(f'{edition}:{n}'.encode())
                    tracks.append({'path':track.name,'prepared_sha256':hashlib.sha256(track.read_bytes()).hexdigest()})
                db.execute('INSERT INTO intake_publications VALUES(?,?,?,?)',
                           ('local:'+edition,edition,'published',json.dumps({'tracks':tracks})))
        def tags(path):
            return dict(self.tags,album='Same Opera', title='Act '+path.stem,work='Same Opera',
                        composer='Same Writer',track=path.stem,disc='1',duration_seconds=100)
        with patch('local_composition.probe',side_effect=tags):
            self.assertEqual(self.store.sync_library(self.root)['probed'],4)
        fields,work = self.store.local('http://example.test/music/opera-b/2.flac',self.root,'/music/')
        self.assertEqual(fields['local_edition'],'local:opera-b')
        resource='http://example.test/music/opera-b/2.flac'
        self.assertEqual(self.store.portable_status(resource,self.root,'/music/')['publication']['path'],'2.flac')
        self.assertEqual(self.store.local_edition(resource,self.root,'/music/'),'local:opera-b')
        edit_document(self.store,self.root,{'kind':'artwork','key':'local:opera-b','before':None,
                      'after':'https://example.test/exact-cover.jpg','source':'Original sleeve'})
        bridge = Bridge({'library_root':str(self.root),'library_resource_prefix':'/music/'},self.base/'state')
        self.assertEqual(bridge.local_art_url({'resource':resource,'album':'Same Opera'}),
                         'https://example.test/exact-cover.jpg')
        self.assertEqual(work['durations'],[100,100])
        self.assertEqual(self.store.match_local(dict(self.tags,title='Act 2',album='Same Opera',
            duration_seconds=100),self.root,'/music/')[1],'local_ambiguous')

    def test_normal_frame_details_uses_same_evidence_with_no_sidecar_fallback(self):
        self.adopt()
        b = Bridge({'library_root':str(self.root),'library_resource_prefix':'/music/','auto_catalog':False},
                   self.base/'state')
        with patch('library_metadata.lookup',side_effect=AssertionError('legacy JSON')), \
             patch.object(b.local_compositions,'context',side_effect=AssertionError('legacy groups')), \
             patch.object(b.local_matches,'lookup',side_effect=AssertionError('legacy index')):
            for resource in (self.resource,'http://proxy.invalid/audirvana/audio_f_99.wav'):
                details=b.details(dict(self.tags,resource=resource))
                self.assertEqual(details['composer'],'Protected composer')
                self.assertEqual(details['catalog_status'],'local_matched')
            self.track.write_bytes(b'different')
            with patch('classical.file_tags', return_value={'composer':'New composer'}):
                d=b.details(dict(self.tags,title='Replacement',resource=self.resource))
                self.assertEqual(d['composer'],'New composer')
                self.assertEqual(d['catalog_status'],'local_unavailable')

    def test_qobuz_exact_ids_retain_prior_source_and_avoid_title_merge(self):
        for aid in ('release1','release2'):
            row={'title':'Same title','duration_seconds':100,'_catalog_album_id':aid,
                 '_catalog_track_id':'7','recordingDate':'1980','recording_source':'Discography'}
            self.store.put_edition(aid,{'7':row},'Shared Album','Artist')
        row={'title':'Changed identity','duration_seconds':100,'_catalog_album_id':'release1','_catalog_track_id':'7'}
        self.store.save(('shared album','artist'),[{'7':row}])
        self.assertEqual(self.store.edition('release1')['7']['recordingDate'],'1980')
        self.assertEqual(self.store.source_outcome('release1')['category'],'pending_identity_conflict')
        self.assertEqual(len(self.store.load(('shared album','artist'))[0]),2)
        self.assertEqual(self.store.edition('release2')['7']['title'],'Same title')
