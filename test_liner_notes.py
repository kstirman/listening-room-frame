import json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from PIL import Image
from bridge import Bridge
import liner_notes,layouts

class Tests(unittest.TestCase):
 def test_matching_scope_and_ambiguity(self):
  with tempfile.TemporaryDirectory() as t:
   p=Path(t)/'notes.json';meta={'album':'Album','artist':'Artist','title':'Song','duration_seconds':245}
   row={'album':'Album','artist':'Artist','review_status':'reviewed','source':'Booklet','fields':{'label':'Label','recordingDate':'2003','releaseDate':'2004','personnel':[{'name':'Wrong','role':'drums'}]},'tracks':[{'title':'Song','duration_seconds':246,'fields':{'personnel':[{'name':'Player','role':'bass'}]}}]}
   p.write_text(json.dumps({'albums':[row]}));cfg={'liner_notes_file':str(p)}
   found=liner_notes.lookup(meta,cfg);self.assertEqual(found['personnel'][0]['name'],'Player');self.assertEqual(found['recordingDate'],'2003');self.assertEqual(found['releaseDate'],'2004')
   for changes in [{'title':'Other'},{'artist':'Other'},{'album':'Other'},{'duration_seconds':260}]:self.assertEqual(liner_notes.lookup(dict(meta,**changes),cfg),{})
   row['tracks'].append(row['tracks'][0]);p.write_text(json.dumps({'albums':[row]}));self.assertEqual(liner_notes.lookup(meta,cfg),{})
 def test_bridge_and_optout(self):
  with tempfile.TemporaryDirectory() as t:
   b=Bridge({},t);meta={'title':'Song','artist':'Artist','album':'Album','duration_seconds':245}
   with patch('liner_notes.lookup',return_value={'personnel':[{'name':'Player','role':'bass'}],'label':'Label'}):
    self.assertIn('liner_notes',b.details(meta))
    b.detail_cache.clear();b.config['liner_notes_enabled']=False;self.assertNotIn('liner_notes',b.details(meta))
 def test_render_long_credits(self):
  with tempfile.TemporaryDirectory() as t:
   p=Path(t);Image.new('RGB',(400,400),'navy').save(p/'art.jpg')
   layouts.liner(p/'art.jpg','Long album name '*12,'Artist',p/'out.jpg',{'display_track':'Long track title '*12,'duration_seconds':245,'liner_notes':{'personnel':[{'name':'A long performer name '*8,'role':'Instrument'}]*12,'songwriters':'Writer '*50,'producer':'Producer '*30,'note':'A very long note '*60,'recordingDate':'circa 1950','releaseDate':'2004','label':'Example'}})
   self.assertEqual(Image.open(p/'out.jpg').size,(3840,2160))

 def test_composer_prefix_and_opera_acts(self):
  import classical,timing
  rows=classical.catalog({str(i):{'genre':'Classical','composer':'Example Composer','title':title,'duration_seconds':100} for i,title in enumerate(['Composer: Opera One: Prelude','Composer: Opera One / Act 1: Scene A','Composer: Opera One / Act 2: Scene B','Composer: Opera Two: Prelude','Composer: Opera Two: Finale'])})
  self.assertEqual(rows['0']['work'],'Opera One')
  self.assertEqual(rows['1']['movementName'],'Act 1: Scene A')
  self.assertEqual(len(timing.composition(rows,'0')['durations']),3)
  self.assertEqual(len(timing.composition(rows,'3')['durations']),2)
 def test_catalog_track_artist(self):
  import auto_catalog
  from unittest.mock import patch
  page='<script type="application/ld+json">{"@type":"Product","name":"Opera","brand":{"name":"Lead Singer"}}</script>'
  with patch('auto_catalog.classical.Tracks') as parser,patch('auto_catalog.classical.catalog',return_value={'1':{'credits':'Orchestra, MainArtist, Orchestra - Writer, Composer'}}):
   self.assertIsNotNone(auto_catalog.album_rows(page,'Opera','Orchestra'))
   self.assertIsNone(auto_catalog.album_rows(page,'Opera','Writer'))
   self.assertIsNone(auto_catalog.album_rows(page,'Wrong','Orchestra'))

 def test_malformed_private_records_fail_closed(self):
  with tempfile.TemporaryDirectory() as t:
   p=Path(t)/'notes.json';meta={'album':'Album','artist':'Artist','title':'Song','duration_seconds':245}
   for value in [None,[],{'albums':None},{'albums':[None,{'review_status':'reviewed','source':'Booklet','album':'Album','artist':'Artist','tracks':[None,{'title':'Song','duration_seconds':245,'fields':None}],'fields':[]}]}]:
    p.write_text(json.dumps(value));self.assertEqual(liner_notes.lookup(meta,{'liner_notes_file':str(p)}),{})
   p.write_text(' '*2_000_001);self.assertEqual(liner_notes.lookup(meta,{'liner_notes_file':str(p)}),{})

 def test_single_opera_identifier_omission_requires_matching_neighbors(self):
  import classical,timing
  def rows(titles):return classical.catalog({str(i):{'genre':'Classical','composer':'Example Composer','title':'Composer: '+t,'duration_seconds':100} for i,t in enumerate(titles)})
  titles=['Opera, WWV 100 / Act 2: First','Opera, Act II: Middle','Opera, WWV 100 / Act 2: Last']
  r=rows(titles);self.assertEqual(len(timing.composition(r,'1')['durations']),3)
  for other in ['Other Opera, WWV 100 / Act 2: Last','Opera, WWV 200 / Act 2: Last','Opera, WWV 100 / Act 3: Last']:
   r=rows(titles[:2]+[other]);self.assertIsNone(timing.composition(r,'1'))
  r=rows(titles+titles);self.assertIsNone(timing.composition(r,'1'))

 def test_catalog_subtitle_requires_exact_visible_heading(self):
  import auto_catalog
  page='<script type="application/ld+json">{"@type":"Product","name":"Opera","brand":{"name":"Singer"}}</script><h1>Opera (Edition Two) by Singer</h1>'
  with patch('auto_catalog.classical.Tracks'),patch('auto_catalog.classical.catalog',return_value={'1':{'credits':'Orchestra, MainArtist'}}):
   self.assertIsNotNone(auto_catalog.album_rows(page,'Opera (Edition Two)','Orchestra'))
   self.assertIsNone(auto_catalog.album_rows(page,'Opera (Edition One)','Orchestra'))
   self.assertIsNone(auto_catalog.album_rows(page,'Opera (Edition Two)','Wrong Artist'))
  search='<script type="application/ld+json">{"@type":"MusicAlbum","name":"Opera","url":"https://www.qobuz.com/us-en/album/opera/id123"}</script>'
  self.assertEqual(auto_catalog.candidates(search,'Opera (Edition Two)'),['id123'])

 def test_reviewed_recording_date_reaches_standard_card(self):
  with tempfile.TemporaryDirectory() as t:
   b=Bridge({},t);meta={'title':'Song','artist':'Artist','album':'Album','duration_seconds':245,'releaseDate':'2020'}
   with patch('liner_notes.lookup',return_value={'recordingDate':'1960-12-16'}):
    result=b.details(meta)
    self.assertEqual(result['recording_year'],'1960')
    self.assertNotIn('release_year',result)
    b.detail_cache.clear();b.config['liner_notes_enabled']=False
    result=b.details(meta)
    self.assertNotIn('recording_year',result)
    self.assertEqual(result['release_year'],'2020')
