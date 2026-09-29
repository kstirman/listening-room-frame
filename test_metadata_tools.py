import json
import tempfile
import unittest
from pathlib import Path
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
