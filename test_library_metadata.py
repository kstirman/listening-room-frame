import json,tempfile,unittest
from pathlib import Path
import library_metadata
class LibraryMetadataTest(unittest.TestCase):
 def test_track_identity_and_refresh(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'.listening-room-metadata';p.mkdir();db=p/'enriched.json'
   db.write_text(json.dumps({'tracks':{'Mixed/01.flac':{'fields':{'composer':'One'}},'Mixed/02.flac':{'fields':{'composer':'Two'}}}}))
   prefix='http://nuc/minimserver/*/AudirvanaLibrary/'
   self.assertEqual(library_metadata.lookup(prefix+'Mixed/01.flac',d),{'composer':'One'})
   self.assertEqual(library_metadata.lookup(prefix+'Mixed/02.flac',d),{'composer':'Two'})
   self.assertEqual(library_metadata.lookup(prefix+'../outside.flac',d),{})
   self.assertEqual(library_metadata.lookup('https://qobuz.com/track',d),{})
   db.write_text(json.dumps({'tracks':{'Mixed/01.flac':{'fields':{'composer':'Updated'}}}}))
   self.assertEqual(library_metadata.lookup(prefix+'Mixed/01.flac',d),{'composer':'Updated'})
if __name__=='__main__':unittest.main()
