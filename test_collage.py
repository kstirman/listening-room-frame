import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from PIL import Image
from bridge import Bridge, MetaParser, metadata_values

class CollageTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory()
  self.b=Bridge({'idle_seconds':120,'local_art_hosts':['nuc'],'collage_interval_seconds':120,'collage_duration_seconds':15,'history_limit':28},self.temp.name)
  self.b.state.update(original='ORIGINAL',owned='ALBUM',album_image='ALBUM',album='qobuz:a',view='album',history=[{'key':'a','cover':'a.jpg'}])
  self.tv=Mock();self.tv.get_artmode.return_value='on'
  self.tv.upload.return_value='COLLAGE'
  self.current='ALBUM'
  self.tv.get_current.side_effect=lambda:{'content_id':self.current}
  self.tv.select_image.side_effect=lambda content:setattr(self,'current',content)
  self.b.details=Mock(return_value=None)
  self.b.television=Mock(return_value=self.tv)
  self.b.playback=Mock(return_value=('PLAYING',{'album_id':'a'}))
  self.b.next_collage=120
 def tearDown(self):self.temp.cleanup()
 def start_collage(self):
  with patch('bridge.layouts.collage'),patch('bridge.time.monotonic',return_value=120):self.b.step()
 def test_timing_and_cached_return(self):
  with patch('bridge.time.monotonic',return_value=119):self.b.step()
  self.tv.upload.assert_not_called()
  self.start_collage()
  self.assertEqual(self.current,'COLLAGE');self.assertEqual(self.b.collage_until,135)
  with patch('bridge.time.monotonic',return_value=134):self.b.step()
  self.assertEqual(self.current,'COLLAGE')
  with patch('bridge.time.monotonic',return_value=135):self.b.step()
  self.assertEqual(self.current,'ALBUM')
  self.assertEqual(self.b.next_collage,240)
  with patch('bridge.time.monotonic',return_value=240):self.b.step()
  self.assertEqual(self.tv.upload.call_count,1)
 def test_new_album_during_collage_returns_latest(self):
  self.start_collage()
  self.b.cover=Mock(return_value=(Path('new.jpg'),'New album'))
  self.tv.upload.return_value='NEW_ALBUM'
  self.b.playback.return_value=('PLAYING',{'album_id':'b'})
  with patch('bridge.time.monotonic',return_value=125):self.b.step()
  self.assertEqual(self.current,'COLLAGE')
  with patch('bridge.time.monotonic',return_value=135):self.b.step()
  self.assertEqual(self.current,'NEW_ALBUM')
  self.tv.delete.assert_called_with('ALBUM')
 def test_restore_from_collage_cleans_both_images(self):
  self.start_collage();self.b.restore()
  self.assertEqual(self.current,'ORIGINAL')
  self.assertEqual({c.args[0] for c in self.tv.delete.call_args_list},{'ALBUM','COLLAGE'})
 def test_manual_art_not_overridden_at_collage_deadline(self):
  self.start_collage();self.current='USER_ART'
  with patch('bridge.time.monotonic',return_value=135):self.b.step()
  self.assertEqual(self.current,'USER_ART')
  self.assertEqual(self.b.blocked_album,'qobuz:a')
 def test_pause_returns_from_collage(self):
  self.start_collage();self.b.playback.return_value=('PAUSED_PLAYBACK',{})
  with patch('bridge.time.monotonic',return_value=125):self.b.step()
  self.assertEqual(self.current,'ALBUM')
 def test_restart_mid_collage_returns_current(self):
  self.start_collage()
  restarted=Bridge(self.b.config,self.temp.name)
  restarted.television=Mock(return_value=self.tv)
  restarted.playback=self.b.playback
  restarted.details=Mock(return_value=None)
  restarted.step()
  self.assertEqual(self.current,'ALBUM')
 def test_history_distinct_capped_persistent(self):
  self.b.state['history']=[]
  blob=io.BytesIO();Image.new('RGB',(20,20),'teal').save(blob,format='JPEG')
  response=Mock(content=blob.getvalue());self.b.session.get=Mock(return_value=response)
  with patch('bridge.layouts.current'):
   for n in list(range(30))+[5]:
    self.b.cover({'album':'Album '+str(n),'albumArtURI':'http://nuc/'+str(n)+'.jpg','artist':'Artist'})
  self.assertEqual(len(self.b.state['history']),28)
  self.assertEqual(self.b.state['history'][0]['title'],'Album 5')
  self.assertEqual(len(list((Path(self.temp.name)/'covers').glob('*.jpg'))),28)
  self.assertEqual(len(Bridge(self.b.config,self.temp.name).state['history']),28)
 def test_classical_roles_do_not_overwrite_performer(self):
  value=metadata_values('<item><artist>Orchestra</artist><artist role="Composer">Composer</artist><artist role="Conductor">Conductor</artist><userAnnotation>tag.Work=Symphony</userAnnotation></item>')
  self.assertEqual(value['artist'],'Orchestra')
  self.assertEqual(value['composer'],'Composer')
  self.assertEqual(value['conductor'],'Conductor')
  self.assertEqual(value['work'],'Symphony')
 def test_qobuz_structured_artist(self):
  p=MetaParser();p.feed('<script type="application/ld+json">{"@type":"Product","name":"Album","brand":{"name":"Artist"}}</script>')
  self.assertEqual(p.structured[0]['brand']['name'],'Artist')
if __name__=='__main__':unittest.main()
