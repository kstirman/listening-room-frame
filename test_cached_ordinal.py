import copy,tempfile,unittest
from unittest.mock import Mock
from bridge import Bridge

class CachedOrdinalTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
  self.b=Bridge({'auto_catalog':False},self.tmp.name)
  self.rows={str(i):dict(title=t,duration_seconds=200,_catalog_album_id='abc',_edition_verified='abc',_catalog_complete=True,_catalog_expected_tracks=2,_source_ordinal=i,album='Album',albumArtist='Artist',work='Work',composer='Composer',composition_group='parts',composition_index=i,composition_count=2) for i,t in enumerate(['Part One (Live)','Part Two (Live)'],1)}
  self.album=dict(album_id='abc',expected_tracks=2,volumes=1,provenance={'source':'JPLAY local music database'},tracks={'999':dict(title='Part Two',version='Live',duration_seconds=200,track_number=2)})
  self.b.music_catalog.edition=Mock(return_value=self.rows)
  self.b.independent_catalog.cache.get=Mock(side_effect=lambda provider,identity:self.album if provider=='jplay' and identity=='abc' else None)
  self.b.album_page=Mock(side_effect=AssertionError('network forbidden'))
  self.m=dict(album_id='abc',object_id='qobuz/track/999',duration_seconds=200)
 def test_normal_entry_and_saved_version(self):
  self.b.independent_catalog.lookup=Mock(return_value={'title':'Part Two'})
  d=self.b.details(self.m)
  self.assertEqual(d['display_track'],'Part Two (Live)');self.assertEqual(d['composition']['index'],1)
  self.b.album_page.assert_not_called()
 def test_multidisc_unique_title_does_not_use_disc_ordinal(self):
  self.album['volumes']=2;self.album['tracks']['999']['track_number']=1
  self.assertEqual(self.b.reviewed_edition_match(self.m)[0],'2')
 def test_identical_titles_require_single_disc_ordinal(self):
  self.rows['1']['title']=self.rows['2']['title']
  self.assertEqual(self.b.reviewed_edition_match(self.m)[0],'2')
  self.album['volumes']=2;self.assertEqual(self.b.reviewed_edition_match(self.m),(None,{}))
 def test_input_conflicts_and_wrong_ids(self):
  for change in [dict(title='Part Two (Studio)'),dict(title='Different'),dict(version='Studio'),dict(duration_seconds=201),dict(object_id='qobuz/track/888'),dict(object_id='other/999'),dict(album_id='other')]:
   self.assertEqual(self.b.reviewed_edition_match(dict(self.m,**change)),(None,{}))
 def test_source_conflicts_and_missing_evidence(self):
  for field,value in [('album_id','other'),('expected_tracks',3),('provenance',None),('tracks',{})]:
   with self.subTest(field=field):
    prior=self.album[field];self.album[field]=value
    self.assertEqual(self.b.reviewed_edition_match(self.m),(None,{}));self.album[field]=prior
  for field,value in [('version','Studio'),('duration_seconds',201),('title','')]:
   prior=self.album['tracks']['999'][field];self.album['tracks']['999'][field]=value
   self.assertEqual(self.b.reviewed_edition_match(self.m),(None,{}));self.album['tracks']['999'][field]=prior
 def test_partial_mixed_and_bad_ordinal_rejected(self):
  self.rows['2']['_source_ordinal']=8;self.assertEqual(self.b.reviewed_edition_match(self.m),(None,{}))
  self.rows['2']['_source_ordinal']=2;self.rows['123456']=self.rows.pop('2');self.assertEqual(self.b.reviewed_edition_match(self.m),(None,{}))
  self.rows['2']=self.rows.pop('123456');self.rows['1']['_catalog_complete']=False;self.assertEqual(self.b.reviewed_edition_match(self.m),(None,{}))
 def test_single_disc_conflicting_position_rejected(self):
  self.album['tracks']['999']['track_number']=1
  self.assertEqual(self.b.reviewed_edition_match(self.m),(None,{}))
 def test_explicit_row_identity_and_version_conflicts(self):
  for field,value in [('_catalog_track_id','888'),('version','Studio')]:
   self.rows['2'][field]=value
   self.assertEqual(self.b.reviewed_edition_match(self.m),(None,{}));self.rows['2'].pop(field)
 def test_numeric_duplicate_not_hidden_on_multidisc(self):
  self.album['volumes']=2;self.rows['1']['title']=self.rows['2']['title']
  self.rows['123456']=self.rows.pop('1')
  self.assertEqual(self.b.reviewed_edition_match(self.m),(None,{}))
 def test_cache_failure_preserves_base_display(self):
  self.b.independent_catalog.cache.get=Mock(side_effect=OSError('unavailable'))
  self.assertEqual(self.b.reviewed_edition_match(self.m),(None,{}))
  self.b.independent_catalog.lookup=Mock(return_value={})
  self.assertIsNotNone(self.b.details(self.m));self.b.album_page.assert_not_called()
 def test_ordinary_match_never_reads_cache(self):
  self.m['title']='Part Two (Live)';self.assertEqual(self.b.reviewed_edition_match(self.m)[0],'2');self.b.independent_catalog.cache.get.assert_not_called()
if __name__=='__main__':unittest.main()
