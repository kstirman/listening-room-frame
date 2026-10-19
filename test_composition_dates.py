import json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import dates,liner_notes
from music_catalog import Store

RULE={'title_match':'A Song','writer_matches':['Example Writer'],'composition_year':'1961–1962','source':'https://example.test/archive'}

class CompositionDatesTests(unittest.TestCase):
 def test_explicit_dates_all_genres_without_classical_details(self):
  for genre in ['Jazz','Rock','Pop','Blues','R&B','Classical','']:
   d=dates.extract({'genre':genre,'compositionDate':'c. 1962','recordingDate':'2001'},work_registry=[],recording_registry={})
   self.assertEqual(d['composition_year'],'c. 1962')
   self.assertEqual(d['recording_year'],'2001')
 def test_song_identity_requires_title_and_writer(self):
  m={'title':'A Song','songwriters':'Example Writer'}
  self.assertEqual(dates.composition_fields(m,registry=[RULE])['composition_year'],'1961–1962')
  for change in [{'title':'A Song (Live)'},{'songwriters':'Different Writer'}]:
   self.assertEqual(dates.composition_fields(dict(m,**change),registry=[RULE]),{})
  conflict=dict(RULE,composition_year='1960')
  self.assertEqual(dates.composition_fields(m,registry=[RULE,conflict]),{})
 def test_recording_and_release_cannot_fill_composition_date(self):
  m={'title':'A Song','releaseDate':'1962','recordingDate':'1961','copyright':'1960'}
  d=dates.catalog_fields(m,[])
  self.assertEqual(d['composition_date_status'],'unknown')
  self.assertNotIn('composition_year',d)
 def test_saved_album_has_per_track_date_and_unknown_status(self):
  with tempfile.TemporaryDirectory() as tmp:
   store=Store(Path(tmp)/'catalog.db')
   with patch.object(store,'document',return_value=[RULE]):
    store.save(('album','artist'),[{'1':{'title':'A Song','composer':'Example Writer'},'2':{'title':'Unknown song'}}])
    rows=store.load(('album','artist'))[0][0]
    self.assertEqual(rows['1']['composition_year'],'1961–1962')
    self.assertEqual(rows['1']['composition_source'],RULE['source'])
    self.assertEqual(rows['2']['composition_date_status'],'unknown')
 def test_album_date_never_becomes_track_composition_date(self):
  data={'albums':[{'album':'Album','artist':'Artist','source':'https://example.test','review_status':'reviewed','fields':{'compositionDate':'1990'},'tracks':[{'title':'Song','duration_seconds':120,'fields':{}}]}]}
  m={'album':'Album','artist':'Artist','title':'Song','duration_seconds':120}
  self.assertNotIn('compositionDate',liner_notes.lookup_data(m,data))
  data['albums'][0]['tracks'][0]['fields']={'compositionDate':'1962','composition_source':RULE['source']}
  self.assertEqual(liner_notes.lookup_data(m,data)['compositionDate'],'1962')
 def test_generic_display_keeps_dates_distinct(self):
  d={'composition_year':'1962','recording_year':'2001','release_year':'2002'}
  self.assertEqual(dates.display_line(d),'Composed · 1962  |  Recorded · 2001')
  self.assertEqual(dates.display_line(d,include_composition=False),'Recorded · 2001')
 def test_removed_registry_date_does_not_linger(self):
  d=dates.catalog_fields({'composition_year':'1962','composition_source':RULE['source']},[])
  self.assertEqual(d['composition_date_status'],'unknown')
  self.assertNotIn('composition_source',d)

if __name__=='__main__':unittest.main()
