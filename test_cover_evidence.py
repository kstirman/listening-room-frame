import io,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from PIL import Image
from cover_evidence import Archive,Unavailable

class EvidenceTest(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
  self.mount=Path(self.temp.name).resolve(); self.root=self.mount/'evidence'
  self.a=Archive(self.root,self.mount)
  self.mock=patch('cover_evidence.os.path.ismount',return_value=True);self.mock.start();self.addCleanup(self.mock.stop)
 def image(self,color='red'):
  b=io.BytesIO();Image.new('RGB',(8,8),color).save(b,format='JPEG');return b.getvalue()
 def test_dedup_versions_and_distinct_albums(self):
  p=self.a.preserve('qobuz:abcd',self.image());m=self.root/'albums/abcd.json';before=m.read_bytes()
  self.a.preserve('qobuz:abcd',self.image());self.assertEqual(before,m.read_bytes())
  self.a.preserve('qobuz:efgh',self.image());self.assertEqual(1,len(list((self.root/'objects').iterdir())))
  self.a.preserve('qobuz:abcd',self.image('blue'));self.assertEqual(2,len(json.loads(m.read_text())['objects']))
 def test_collision_and_bad_identity(self):
  p=self.a.preserve('qobuz:abcd',self.image());(self.root/p).write_bytes(b'corrupted')
  with self.assertRaises(Unavailable):self.a.preserve('qobuz:abcd',self.image())
  for key in ['spotify:abcd','qobuz:../abcd','qobuz:']:
   with self.assertRaises(Unavailable):self.a.preserve(key,self.image())
 def test_unmounted_does_not_create_root(self):
  with patch('cover_evidence.os.path.ismount',return_value=False):
   with self.assertRaises(Unavailable):self.a.preserve('qobuz:abcd',self.image())
  self.assertFalse(self.root.exists())
 def test_traversal_and_symlink(self):
  self.a.preserve('qobuz:abcd',self.image());m=json.loads((self.root/'albums/abcd.json').read_text());m['objects'][0]['path']='../outside'
  with self.assertRaises(Unavailable):self.a.verify_manifest(self.root,m)
  (self.root/'albums/efgh.json').symlink_to(self.mount/'elsewhere')
  with self.assertRaises(Unavailable):self.a.preserve('qobuz:efgh',self.image())
 def test_source_url_is_not_metadata_or_secret(self):
  self.a.preserve('qobuz:abcd',self.image(),'https://static.qobuz.com/images/covers/cd/ab/abcd_600.jpg?token=secret')
  m=json.loads((self.root/'albums/abcd.json').read_text());self.assertNotIn('source_url',m['objects'][0]);self.assertNotIn('title',m)
 def test_backup_restore(self):
  self.a.preserve('qobuz:abcd',self.image());out=self.mount/'backup.zip';self.a.backup(out)
  import zipfile
  restore=self.mount/'restored'
  with zipfile.ZipFile(out) as z:z.extractall(restore)
  self.assertTrue(self.a.verify_manifest(restore,json.loads((restore/'albums/abcd.json').read_text())))
  self.assertTrue((restore/'RESTORE.json').is_file())

class HistoryTest(unittest.TestCase):
 setUp = EvidenceTest.setUp
 image = EvidenceTest.image
 def bridge(self):
  import importlib.util
  spec=importlib.util.spec_from_file_location('candidate_bridge',Path(__file__).with_name('bridge.py'));mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod);Bridge=mod.Bridge
  b=Bridge.__new__(Bridge);b.root=self.mount/'state';b.root.mkdir();(b.root/'covers').mkdir();b.state={'history':[]};b.state_file=b.root/'state.json';b.evidence_archive=self.a
  return b
 def entry(self,b,index):
  key='qobuz:album'+str(index);path='covers/'+str(index)+'.jpg';(b.root/path).write_bytes(self.image())
  return {'key':key,'cover':path,'title':'','artist':''}
 def test_twenty_ninth_and_backfill(self):
  b=self.bridge();entries=[self.entry(b,i) for i in range(29)];b.state['history']=entries[:28];b.evidence_backfill()
  self.assertEqual(28,len(list((self.root/'albums').glob('*.json'))))
  b.state['history']=entries[1:];b.evidence_evict(entries[:1]);self.assertFalse((b.root/entries[0]['cover']).exists())
  self.assertTrue((self.root/'albums/album0.json').exists());self.assertEqual(28,len(b.state['history']))
 def test_failed_archive_preserves_sole_cover_and_retries(self):
  b=self.bridge();entry=self.entry(b,1)
  with patch('cover_evidence.os.path.ismount',return_value=False):b.evidence_evict([entry])
  self.assertTrue((b.root/entry['cover']).exists());self.assertEqual([entry],b.state['evidence_pending'])
  b.evidence_backfill();self.assertFalse((b.root/entry['cover']).exists());self.assertEqual([],b.state['evidence_pending'])
 def test_low_space_and_restore_symlink(self):
  from types import SimpleNamespace
  with patch('cover_evidence.os.statvfs',return_value=SimpleNamespace(f_bavail=0,f_frsize=4096)):
   with self.assertRaises(Unavailable):self.a.preserve('qobuz:abcd',self.image())
  self.a.preserve('qobuz:abcd',self.image());m=json.loads((self.root/'albums/abcd.json').read_text())
  (self.root/'objects').rename(self.root/'elsewhere');(self.root/'objects').symlink_to(self.root/'elsewhere',target_is_directory=True)
  with self.assertRaises(Unavailable):self.a.verify_manifest(self.root,m)
 def test_budget_keeps_old_evidence_and_transient_display(self):
  from unittest.mock import Mock
  b=self.bridge();b.config={'history_limit':28};b.reviewed_edition_album=lambda m:{'_album_art_url':'https://static.qobuz.com/images/covers/cd/ab/abcd_600.jpg'}
  b.album_key=lambda m:'qobuz:abcd';b.session=Mock();b.session.get.return_value.content=self.image();b.session.get.return_value.raise_for_status.return_value=None
  old=b.root/'covers/old.jpg';old.write_bytes(b'old evidence'+b'\0'*(16*1024*1024))
  with patch('cover_evidence.os.path.ismount',return_value=False),patch('layouts.current'):
   b.cover({'album_id':'abcd','album':'Example'})
  self.assertTrue(old.exists());self.assertEqual([],b.state['history']);self.assertEqual('unavailable_cache_budget_reached',b.state['evidence_status']);self.assertTrue((b.root/'evidence-transient.jpg').exists())

 def test_stale_pending_metadata_never_deletes_live_path(self):
  b=self.bridge();entry=self.entry(b,1);b.state['history']=[dict(entry,title='Enriched')];b.state['evidence_pending']=[entry]
  b.evidence_backfill();self.assertTrue((b.root/entry['cover']).exists());self.assertEqual([],b.state['evidence_pending'])
 def test_pending_cleanup_rejects_unsafe_paths_for_every_source(self):
  b=self.bridge();outside=self.mount/'outside.jpg';outside.write_bytes(self.image());link=b.root/'covers/link.jpg';link.symlink_to(outside)
  b.state['evidence_pending']=[{'key':'local:example','cover':str(outside)},{'key':'local:example','cover':'../outside.jpg'},{'key':'local:example','cover':'covers/link.jpg'}]
  b.evidence_backfill();self.assertTrue(outside.exists());self.assertTrue(link.is_symlink())

 def test_backup_rejects_symlink_album_directory(self):
  self.a.preserve('qobuz:abcd',self.image());(self.root/'albums').rename(self.root/'elsewhere');(self.root/'albums').symlink_to(self.root/'elsewhere',target_is_directory=True)
  with self.assertRaises(Unavailable):self.a.backup(self.mount/'backup.zip')
  self.assertFalse((self.mount/'backup.zip').exists())

if __name__=="__main__":unittest.main()
