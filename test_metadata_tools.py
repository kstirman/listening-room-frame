import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import library_enrich
from metadata_tools import apply, audit

class MetadataToolsTests(unittest.TestCase):
    def test_preserve_verified_fields_and_override_and_backup(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); (root/'track.flac').touch()
            store = root/'.listening-room-metadata'; store.mkdir()
            db = store/'enriched.json'
            db.write_text(json.dumps({'tracks': {'track.flac': {'fields': {'composer': 'Verified'}}}}))
            (store/'track-overrides.json').write_text(json.dumps({'track.flac': {'fields': {'conductor': 'Protected'}}}))
            fields = {'composer':'Wrong', 'conductor':'Wrong', 'recordingDate':'1959'}
            proposal = {'tracks': {'track.flac': {'fields':fields, 'provenance':dict.fromkeys(fields, 'Original liner notes')}}}
            self.assertEqual(apply(root, proposal), 1)
            self.assertEqual(apply(root, proposal), 0)
            self.assertEqual(json.loads(db.read_text())['tracks']['track.flac']['fields'], {'composer':'Verified','recordingDate':'1959'})
            self.assertEqual(len(list(store.glob('enriched.before-*'))),1)
            self.assertEqual(stat.S_IMODE(store.stat().st_mode), 0o700)
            self.assertTrue(all(stat.S_IMODE(path.stat().st_mode) == 0o600 for path in store.iterdir()))
    def test_reject_escape_without_writing(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); (root/'.listening-room-metadata').mkdir()
            with self.assertRaises(ValueError):
                apply(root, {'tracks': {'../escape.flac': {'fields': {'composer':'X'},'provenance':{'composer':'Source'}}}})
            self.assertFalse((root/'.listening-room-metadata/enriched.json').exists())
    def test_audit_includes_crossover_and_overrides(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); store=root/'.listening-room-metadata';store.mkdir()
            (store/'inventory.json').write_text(json.dumps({'albums':[{'cover':'cover.jpg','tracks':[{'path':'x.flac','title':'Piece','tags':{'genre':['Jazz']}}]}]}))
            (store/'track-overrides.json').write_text(json.dumps({'x.flac':{'fields':{'classical':True,'composer':'Composer'}}}))
            row=audit(root)['tracks'][0]
            self.assertTrue(row['classical']);self.assertNotIn('composer',row['missing_candidates'])
            self.assertIn('compositionDate',row['missing_candidates'])

    def test_inventory_storage_hardens_existing_artifacts(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);store=root/'.listening-room-metadata';cache=store/'http-cache';art=store/'covers'
            cache.mkdir(parents=True, mode=0o755);art.mkdir(mode=0o755)
            old=cache/'response.json';old.write_text('{}');old.chmod(0o644)
            with patch.multiple(library_enrich, STORE=store, CACHE=cache, ART=art):
                library_enrich.init_storage()
                library_enrich.private_write_text(store/'inventory.json', '{}')
                library_enrich.save_new(art/'cover.jpg', b'image')
            self.assertTrue(all(stat.S_IMODE(path.stat().st_mode) == 0o700 for path in (store,cache,art)))
            self.assertTrue(all(stat.S_IMODE(path.stat().st_mode) == 0o600 for path in (old,store/'inventory.json',art/'cover.jpg')))

    def test_reject_foreign_owned_artifact_before_chmod(self):
        with tempfile.TemporaryDirectory() as directory:
            store=Path(directory)/'.listening-room-metadata';store.mkdir();artifact=store/'inventory.json';artifact.write_text('{}');artifact.chmod(0o644)
            uid=os.getuid()
            with patch('library_enrich.os.getuid', side_effect=[uid,uid+1]):
                with self.assertRaises(PermissionError):
                    library_enrich.secure_storage(store)
            self.assertEqual(stat.S_IMODE(artifact.stat().st_mode), 0o644)
