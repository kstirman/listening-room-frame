import json,sqlite3,unittest
from encounter_captions import encountered_album
class TestEncounterCaptions(unittest.TestCase):
 def setUp(self):
  self.db=sqlite3.connect(':memory:');self.db.execute('create table encountered_albums(identity text,album_id text,payload text)')
 def tearDown(self):self.db.close()
 def save(self,p,aid='abc123',identity='qobuz:abc123'):
  p.setdefault('source','qobuz');self.db.execute('insert into encountered_albums values(?,?,?)',(identity,aid,json.dumps(p)))
 def test_album_only(self):
  self.save(dict(album_id='abc123',album='Saved Album',artist='Performer',_album_identity_fields=['album','artist'],title='Wrong current track',composer='Do not infer',recordingDate='1950',duration_seconds=100))
  self.assertEqual(encountered_album(self.db,{'album_id':'abc123'}),{'album':'Saved Album','artist':'Performer'})
 def test_no_cross_album(self):
  self.save(dict(album_id='abc123',album='Saved'))
  self.assertEqual(encountered_album(self.db,{'album_id':'def456'}),{})
 def test_payload_conflict(self):
  self.save(dict(album_id='def456',album='Saved'))
  self.assertEqual(encountered_album(self.db,{'album_id':'abc123'}),{})
 def test_existing_names_win(self):
  self.save(dict(album_id='abc123',album='Old',artist='Old artist'))
  self.assertEqual(encountered_album(self.db,{'album_id':'abc123','album':'Current','artist':'Current artist'}),{})
 def test_malformed_id(self):
  self.assertEqual(encountered_album(self.db,{'album_id':'https://example.com/?token=x'}),{})
 def test_other_provider(self):
  self.save(dict(album_id='abc123',album='Saved'))
  self.assertEqual(encountered_album(self.db,{'source':'spotify','album_id':'abc123'}),{})
 def test_corrupt_payload(self):
  self.db.execute("insert into encountered_albums values('qobuz:abc123','abc123','invalid')")
  self.assertEqual(encountered_album(self.db,{'album_id':'abc123'}),{})
 def test_blank_or_nonstring(self):
  self.save(dict(album_id='abc123',album=' ',artist=['bad']))
  self.assertEqual(encountered_album(self.db,{'album_id':'abc123'}),{})
 def test_unproven_source(self):
  self.save(dict(album_id='abc123',album='Saved',source=''))
  self.assertEqual(encountered_album(self.db,{'album_id':'abc123'}),{})
 def test_reviewed_provenance(self):
  self.save(dict(album_id='abc123',album='Saved',source='',_album_identity_source='https://label.example/release',_album_identity_evidence='Exact cover verified',_album_identity_fields=['album']))
  self.assertEqual(encountered_album(self.db,{'album_id':'abc123'}),{'album':'Saved'})
 def test_opaque_name_rejected(self):
  self.save(dict(album_id='abc123',album='abc123',artist='qobuz/track/123'))
  self.assertEqual(encountered_album(self.db,{'album_id':'abc123'}),{})
 def test_current_artist_blocks_albumartist(self):
  self.save(dict(album_id='abc123',albumArtist='Old'))
  self.assertEqual(encountered_album(self.db,{'album_id':'abc123','artist':'New'}),{})
 def test_title_proof_does_not_bless_artist(self):
  self.save(dict(album_id='abc123',album='Saved',artist='Unproven'))
  self.assertEqual(encountered_album(self.db,{'album_id':'abc123'}),{'album':'Saved'})
 def test_reviewed_without_field_scope(self):
  self.save(dict(album_id='abc123',album='Saved',artist='Unproven',source='',_album_identity_source='https://label.example/release',_album_identity_evidence='Unscoped prior review'))
  self.assertEqual(encountered_album(self.db,{'album_id':'abc123'}),{})
if __name__=='__main__':unittest.main()
