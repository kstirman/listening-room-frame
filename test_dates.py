import unittest,json
from unittest.mock import patch
from dates import extract,years

class DateTests(unittest.TestCase):
 def setUp(self):
  fixtures={'recording-dates.json': '{"qobuz/track/271252285": {"recording_year": "1970", "recording_source": "https://www.celibidache.it/?page_id=88"}, "qobuz/track/2270897": {"recording_year": "2003", "recording_source": "https://charm.kcl.ac.uk/pubs/DeccaH.pdf"}, "qobuz/track/2270903": {"recording_year": "2000", "recording_source": "https://charm.kcl.ac.uk/pubs/DeccaH.pdf"}}', 'work-dates.json': '[{"composer_match": "Bruckner", "work_pattern": "\\\\bWAB\\\\s*104\\\\b", "composition_year": "1874", "source": "https://www.laphil.com/works/symphony-no-4-romantic", "note": "Original composition year. Subsequent versions/revisions are separate and are retained from source track titles."}, {"composer_match": "Sibelius", "work_pattern": "\\\\bOp\\\\.\\\\s*82\\\\b", "composition_year": "1915; revised 1916, 1919", "source": "https://sibelius.klubi.fi/english/musiikki/ork_sinf_05.htm", "note": "Original version and later revisions explicitly distinguished."}, {"composer_match": "Beethoven", "work_pattern": "(?i)\\\\b(?:symphony|symphonie|sinfonie)\\\\s*(?:no\\\\.?|nr\\\\.?)\\\\s*1\\\\b", "composition_year": "1799\\u20131800", "source": "https://www.bso.org/works/beethoven-the-nine-symphonies"}, {"composer_match": "Beethoven", "work_pattern": "(?i)\\\\b(?:symphony|symphonie|sinfonie)\\\\s*(?:no\\\\.?|nr\\\\.?)\\\\s*2\\\\b", "composition_year": "1801\\u20131802", "source": "https://www.bso.org/works/beethoven-the-nine-symphonies"}, {"composer_match": "Beethoven", "work_pattern": "(?i)\\\\b(?:symphony|symphonie|sinfonie)\\\\s*(?:no\\\\.?|nr\\\\.?)\\\\s*3\\\\b", "composition_year": "1803", "source": "https://www.bso.org/works/beethoven-the-nine-symphonies"}, {"composer_match": "Beethoven", "work_pattern": "(?i)\\\\b(?:symphony|symphonie|sinfonie)\\\\s*(?:no\\\\.?|nr\\\\.?)\\\\s*4\\\\b", "composition_year": "1806", "source": "https://www.bso.org/works/beethoven-the-nine-symphonies"}, {"composer_match": "Beethoven", "work_pattern": "(?i)\\\\b(?:symphony|symphonie|sinfonie)\\\\s*(?:no\\\\.?|nr\\\\.?)\\\\s*5\\\\b", "composition_year": "1807\\u20131808", "source": "https://www.bso.org/works/beethoven-the-nine-symphonies"}, {"composer_match": "Beethoven", "work_pattern": "(?i)\\\\b(?:symphony|symphonie|sinfonie)\\\\s*(?:no\\\\.?|nr\\\\.?)\\\\s*6\\\\b", "composition_year": "1808", "source": "https://www.bso.org/works/beethoven-the-nine-symphonies"}, {"composer_match": "Beethoven", "work_pattern": "(?i)\\\\b(?:symphony|symphonie|sinfonie)\\\\s*(?:no\\\\.?|nr\\\\.?)\\\\s*7\\\\b", "composition_year": "1811\\u20131812", "source": "https://www.bso.org/works/beethoven-the-nine-symphonies"}, {"composer_match": "Beethoven", "work_pattern": "(?i)\\\\b(?:symphony|symphonie|sinfonie)\\\\s*(?:no\\\\.?|nr\\\\.?)\\\\s*8\\\\b", "composition_year": "1812", "source": "https://www.bso.org/works/beethoven-the-nine-symphonies"}, {"composer_match": "Beethoven", "work_pattern": "(?i)\\\\b(?:symphony|symphonie|sinfonie)\\\\s*(?:no\\\\.?|nr\\\\.?)\\\\s*9\\\\b", "composition_year": "1822\\u20131824", "source": "https://www.bso.org/works/beethoven-the-nine-symphonies"}, {"composer_match": "Mozart", "work_pattern": "(?i)\\\\bK(?:V)?\\\\.?\\\\s*385\\\\b", "composition_year": "1782", "source": "https://www.themorgan.org/music-manuscripts-and-printed-music/115426"}, {"composer_match": "Mozart", "work_pattern": "(?i)\\\\bK(?:V)?\\\\.?\\\\s*504\\\\b", "composition_year": "1786", "source": "https://kv.mozarteum.at/de/work/sinfonie-in-d-5901"}, {"composer_match": "Mozart", "work_pattern": "(?i)\\\\bK(?:V)?\\\\.?\\\\s*525\\\\b", "composition_year": "1787", "source": "https://kv.mozarteum.at/de/work/eine-kleine-nachtmusik-6049"}, {"composer_match": "Mozart", "work_pattern": "(?i)\\\\bK(?:V)?\\\\.?\\\\s*543\\\\b", "composition_year": "1788", "source": "https://kv.mozarteum.at/de/work/sinfonie-in-es-6244"}, {"composer_match": "Mozart", "work_pattern": "(?i)\\\\bK(?:V)?\\\\.?\\\\s*550\\\\b", "composition_year": "1788", "source": "https://kv.mozarteum.at/de/work/sinfonie-in-g-11586"}, {"composer_match": "Mozart", "work_pattern": "(?i)\\\\bK(?:V)?\\\\.?\\\\s*551\\\\b", "composition_year": "1788", "source": "https://kv.mozarteum.at/de/work/sinfonie-in-es-6244"}, {"composer_match": "Mozart", "work_pattern": "(?i)\\\\bK(?:V)?\\\\.?\\\\s*425\\\\b", "composition_year": "1783; revised 1785", "source": "https://kv.mozarteum.at/de/work/sinfonie-in-c-12050"}, {"composer_match": "Mahler", "work_pattern": "\\\\b(?:Symphony|Symphonie)\\\\s*(?:No\\\\.?|Nr\\\\.?)\\\\s*3\\\\b", "composition_year": "1895\\u20131896", "source": "https://www.laphil.com/works/symphony-no-3-mahler"}]'}
  self.reader=patch("dates.Path.read_text",lambda path:fixtures[path.name])
  self.reader.start();self.addCleanup(self.reader.stop)
 def test_combined_works_keep_dates_attached(self):
  value='Rienzi: 1837–1840; Lohengrin: 1845–1848'
  self.assertEqual(extract({'compositionDate':value,'recordingDate':'1978-03'}, {'composer':'Wagner'})['composition_year'],value)
  value='Otello: 1884–1886; Pagliacci: 1892'
  self.assertEqual(extract({'compositionDate':value}, {'composer':'Verdi; Leoncavallo'})['composition_year'],value)
  self.assertNotIn('composition_year',extract({'compositionDate':'First: unknown; Second: 1892'}, {'composer':'Unknown'}))
 def test_release_copyright_not_recording(self):
  self.assertEqual(extract({'date':'2020','releaseDate':'2020','originaldate':'1959','copyright':'1959'}),{'release_year':'1959','release_label':'Originally released'})
 def test_release_fallback(self):
  self.assertEqual(extract({'releaseDate':'2004-04-02'}),{'release_year':'2004','release_label':'Released'})
  self.assertEqual(extract({'copyright':'2004'}),{})
  self.assertEqual(extract({'recordingDate':'2003','releaseDate':'2004'}),{'recording_year':'2003'})
 def test_mahler_sessions_distinct(self):
  self.assertEqual(extract({'object_id':'qobuz/track/2270897'})['recording_year'],'2003')
  self.assertEqual(extract({'object_id':'qobuz/track/2270903'})['recording_year'],'2000')
 def test_mahler(self):
  self.assertEqual(extract({}, {'composer':'Gustav Mahler','work':'Mahler: Symphony No. 3 in D Minor / Pt. 1'})['composition_year'],'1895–1896')
 def test_iso_date_and_range(self):
  self.assertEqual(years('1988-10-16'),'1988');self.assertEqual(years('2001-02'),'2001');self.assertEqual(years('1959–1960'),'1959–1960')
 def test_historical_uncertainty_survives_display(self):
  self.assertEqual(extract({'compositionDate':'c. 1783'},{'composer':'Haydn'})['composition_year'],'c. 1783')
  self.assertEqual(years('probably 1823–1828'),'probably 1823–1828')
  self.assertEqual(years('before 1714'),'before 1714')
  self.assertEqual(extract({'compositionDate':'by 1735'},{'composer':'Bach'})['composition_year'],'by 1735')
  self.assertEqual(years('by unknown'),'')
  self.assertEqual(years('circa unknown'),'')
 def test_century_periods_survive_composition_display(self):
  for period in ['13th century or earlier','late 13th–early 14th century','12th century']:
   self.assertEqual(extract({'compositionDate':period},{'composer':'Anonymous'})['composition_year'],period)
  self.assertEqual(years('late 13th - early 14th century'),'late 13th–early 14th century')
  self.assertEqual(years('unknown century'),'')
  self.assertEqual(years('a 13th century song'),'')
 def test_bruckner_recording_not_version(self):
  m={'title':'Symphony: I. Bewegt (1881 Haas Version) [Live at Munich, 16.X.1988]'}
  d=extract(m,{'composer':'Anton BRUCKNER','work':'Symphony No. 4, WAB 104'})
  self.assertEqual(d['recording_year'],'1988');self.assertEqual(d['composition_year'],'1874')
 def test_no_guess_from_unlabelled_year(self):
  self.assertEqual(extract({'title':'Song (2020 Remaster)'}),{})
 def test_explicit_composition_overrides_catalog(self):
  d=extract({'compositionDate':'1878–1880','recordingDate':'1988'},{'composer':'Bruckner','work':'WAB 104'})
  self.assertEqual(d['composition_year'],'1878–1880')
 def test_non_classical_explicit_recording(self):
  self.assertEqual(extract({'recordingDate':'1962-05-01'}),{'recording_year':'1962'})
 def test_recording_override_scoped_to_track(self):
  self.assertEqual(extract({'object_id':'qobuz/track/271252285'})['recording_year'],'1970')
  self.assertEqual(extract({'object_id':'qobuz/track/271252288'}),{})
 def test_sibelius_work_date(self):
  d=extract({}, {'composer':'Jean Sibelius','work':'Symphony No.5, Op. 82'})
  self.assertEqual(d['composition_year'],'1915; revised 1916, 1919')
if __name__=='__main__':unittest.main()
