import copy, unittest
from unittest.mock import Mock
import test_cached_ordinal
class WitnessedDurationTests(unittest.TestCase):
 def setUp(self):
  test_cached_ordinal.CachedOrdinalTests.setUp(self)
  self.rows['1'].update(artist='Performer',duration_seconds=243,_observed_duration_seconds=242,compositionDate='1900',recordingDate='2000')
  self.rows['2'].update(artist='Performer',duration_seconds=300)
  self.m=dict(album_id='abc',artist='Performer',title='Part One (Live)',duration_seconds=242)
  self.b.independent_catalog.lookup=Mock(return_value={})
 def test_normal_display(self):
  d=self.b.details(self.m);self.assertEqual(d['recording_year'],'2000');self.assertEqual(d['composition_year'],'1900');self.b.album_page.assert_not_called()
 def test_required_evidence(self):
  for field,value in [('title',''),('artist',''),('artist','Other'),('title','Other'),('duration_seconds',241),('album_id','other'),('version','Studio')]:
   self.assertEqual(self.b.reviewed_edition_match(dict(self.m,**{field:value})),(None,{}))
 def test_row_evidence_and_partial(self):
  for field,value in [('_observed_duration_seconds',None),('_source_ordinal',9),('_catalog_track_id','123'),('_catalog_complete',False),('_edition_verified','other')]:
   old=copy.deepcopy(self.rows['1']);self.rows['1'][field]=value
   self.assertEqual(self.b.reviewed_edition_match(self.m),(None,{}));self.rows['1']=old
 def test_duplicate_name_rejected(self):
  self.rows['2']['title']=self.rows['1']['title'];self.assertEqual(self.b.reviewed_edition_match(self.m),(None,{}))
 def test_exact_duration_precedence(self):
  self.rows['2'].update(title=self.rows['1']['title'],duration_seconds=242)
  self.assertEqual(self.b.reviewed_edition_match(self.m)[0],'2')
 def test_whitespace_only_normalization(self):
  self.m.update(title='  Part  One (Live) ',artist=' Performer ')
  self.assertEqual(self.b.reviewed_edition_match(self.m)[0],'1')
