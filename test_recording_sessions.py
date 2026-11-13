import copy, tempfile, unittest
from pathlib import Path
from unittest.mock import patch
from PIL import Image
import dates, layouts

class Tests(unittest.TestCase):
 def test_range_and_later_iso_session_preserved(self):
  metadata={'recordingDate':'1940–1947; 1949-11-26','compositionDate':'Original: 1817; Arranged: 1828','releaseDate':'1998'}
  original=copy.deepcopy(metadata)
  result=dates.extract(metadata,work_registry=[],recording_registry={})
  self.assertEqual(result['recording_year'],'1940–1947; 1949')
  self.assertEqual(result['composition_year'],'Original: 1817; Arranged: 1828')
  self.assertEqual(metadata,original)
  self.assertIn('Recorded · 1940–1947; 1949',dates.display_line(result))
 def test_per_session_iso_ranges_qualifiers_and_duplicate_years(self):
  for raw,expected in [('1951-05-04; 1953-06-01','1951; 1953'),('c. 1930–1932; probably 1935','c. 1930–1932; probably 1935'),('1950-01-01; 1950-09-01','1950'),('1940–1947; 1949–1951','1940–1947; 1949–1951')]:
   self.assertEqual(dates.recording_years(raw),expected)
 def test_single_session_and_other_date_semantics_unchanged(self):
  for raw in ['1949-11-26','c. 1930–1932','1970','before 1900','']:
   self.assertEqual(dates.recording_years(raw),dates.years(raw))
  self.assertEqual(dates.composition_years('Original: 1817; Revised: c. 1826–1828'),'Original: 1817; Revised: c. 1826–1828')
  self.assertEqual(dates.extract({'releaseDate':'2001'},work_registry=[],recording_registry={})['release_year'],'2001')
  self.assertNotIn('recording_year',dates.extract({'title':'[Remastered 2001; 2002]','releaseDate':'2003'},work_registry=[],recording_registry={}))
 def test_labelled_live_sessions_and_classical_render(self):
  result=dates.extract({'title':'Recital [Recorded 1940–1947; 1949-11-26]'},work_registry=[],recording_registry={})
  self.assertEqual(result['recording_year'],'1940–1947; 1949')
  with tempfile.TemporaryDirectory() as t:
   p=Path(t);Image.new('RGB',(100,100),'navy').save(p/'cover.png');calls=[];original=layouts.Column.block
   def record(col,value,*args,**kwargs):calls.append(value);return original(col,value,*args,**kwargs)
   with patch.object(layouts.Column,'block',record):layouts.classical(p/'cover.png',dict(result,work='Combined recital'),p/'out.png')
   self.assertIn('Recorded · 1940–1947; 1949',calls)

if __name__=='__main__':unittest.main()
