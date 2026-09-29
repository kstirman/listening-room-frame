import tempfile, unittest
from unittest.mock import Mock, patch
from pathlib import Path
from bridge import Bridge, metadata_values

class Tests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory()
  self.b=Bridge({'idle_seconds':0,'local_art_hosts':['192.0.2.10']},self.temp.name)
  self.tv=Mock(); self.tv.get_artmode.return_value='on'
  self.b.television=Mock(return_value=self.tv)
  self.b.details=Mock(return_value=None)
  self.b.cover=Mock(return_value=(Path('cover.jpg'),'Album'))
 def tearDown(self):self.temp.cleanup()
 def test_local_art_override_replaces_embedded_cover_url(self):
  import io
  from PIL import Image
  metadata={'album':'Album','resource':'http://192.0.2.10/music/A/1.flac','albumArtURI':'http://192.0.2.10/embedded.jpg'}
  chosen='http://192.0.2.10/chosen.jpg'
  self.b.config['local_art_overrides']={Bridge.album_key(metadata):chosen}
  data=io.BytesIO();Image.new('RGB',(32,32),'red').save(data,format='JPEG')
  self.b.session=Mock();self.b.session.get.return_value.content=data.getvalue()
  Bridge.cover(self.b,metadata)
  self.b.session.get.assert_called_once_with(chosen,timeout=15)
 def test_saved_metadata_survives_unreadable_audio_tags(self):
  self.b.config['library_root']='/unused'
  saved={'classical':True,'composer':'Ludwig van Beethoven','work':'Symphony No. 6','conductor':'Arturo Toscanini','recordingDate':'1952','compositionDate':'1808'}
  with patch('bridge.classical.file_tags',side_effect=RuntimeError('unreadable audio')), patch('bridge.library_metadata.lookup',return_value=saved) as lookup:
   result=Bridge.details(self.b,{'resource':'http://192.0.2.10/minimserver/*/AudirvanaLibrary/test.aiff'})
  lookup.assert_called_once()
  self.assertEqual(result['conductor'],'Arturo Toscanini')
  self.assertEqual(result['recording_year'],'1952')
  self.assertEqual(result['composition_year'],'1808')
 def test_qobuz_release_date_reaches_display(self):
  self.b.album_page=Mock(return_value='<script type="application/ld+json">{"@type":"MusicAlbum","datePublished":"2004-04-02"}</script>')
  result=Bridge.details(self.b,{'object_id':'qobuz/track/999','album_id':'abc'})
  self.assertEqual(result,{'release_year':'2004','release_label':'Released'})
 def test_reviewed_captions_survive_missing_tags_and_cached_blank_history(self):
  self.b.config['library_root']='/unused'
  metadata={'resource':'http://192.0.2.10/minimserver/*/AudirvanaLibrary/A/1.flac','albumArtURI':'http://192.0.2.10/cover.jpg'}
  with patch('bridge.classical.file_tags',return_value={}), patch('bridge.library_metadata.lookup',return_value={'album':"Somethin' Else",'artist':'Cannonball Adderley','recordingDate':'1958'}):
   details=Bridge.details(self.b,metadata)
  key=Bridge.album_key(metadata)
  self.b.state.update(album=key,history=[{'key':key,'cover':'cover.jpg','title':'','artist':''}])
  with patch('bridge.layouts.current') as draw:
   _,title=Bridge.card(self.b,metadata,details)
  self.assertEqual(title,"Somethin' Else")
  self.assertEqual(draw.call_args.args[1:3],("Somethin' Else",'Cannonball Adderley'))
  self.assertIn('1958',draw.call_args.kwargs['date_line'])
 def test_reviewed_artist_replaces_wrong_cached_artist(self):
  self.b.config['library_root']='/unused'
  metadata={'album':'Paris Jazz Concert 1962','albumArtist':'Bloodgood','resource':'http://192.0.2.10/minimserver/*/AudirvanaLibrary/A/1.flac','albumArtURI':'http://192.0.2.10/cover.jpg'}
  with patch('bridge.classical.file_tags',return_value={}), patch('bridge.library_metadata.lookup',return_value={'albumArtist':'Louis Armstrong & His All-Stars'}):
   details=Bridge.details(self.b,metadata)
  key=Bridge.album_key(metadata)
  self.b.state.update(album=key,history=[{'key':key,'cover':'cover.jpg','title':metadata['album'],'artist':'Bloodgood'}])
  with patch('bridge.layouts.current') as draw:Bridge.card(self.b,metadata,details)
  self.assertEqual(draw.call_args.args[2],'Louis Armstrong & His All-Stars')
 def test_qobuz_ids_not_stream_credentials(self):
  m=metadata_values('<item id="qobuz/track/1" parentID="qobuz/album/abc"><res>secret</res></item>')
  self.assertEqual(Bridge.album_key(m),'qobuz:abc')
 def test_local_album_stable_across_embedded_track_covers(self):
  a={'album':'Album','resource':'http://192.0.2.10/music/A/1.flac','albumArtURI':'http://192.0.2.10/1.jpg'}
  b=dict(a,resource='http://192.0.2.10/music/A/2.flac',albumArtURI='http://192.0.2.10/2.jpg')
  self.assertEqual(Bridge.album_key(a),Bridge.album_key(b))
 def test_tv_mode_untouched(self):
  self.b.playback=Mock(return_value=('PLAYING',{'album_id':'abc'}))
  self.tv.get_artmode.return_value='off'
  self.b.step(); self.tv.upload.assert_not_called(); self.tv.select_image.assert_not_called()
 def test_restores_only_own_image(self):
  self.b.state={'owned':'MY_X','original':'SAM_A','album':'abc'}
  self.tv.get_current.return_value={'content_id':'SAM_MANUAL'}
  self.b.restore(); self.tv.select_image.assert_not_called(); self.tv.delete.assert_called_once_with('MY_X')
 def test_no_cover_restores_instead_of_stale_album(self):
  self.b.playback=Mock(return_value=('PLAYING',{}))
  self.b.restore=Mock(); self.b.step(); self.b.restore.assert_called_once()
 def test_same_album_not_uploaded(self):
  self.b.state={'owned':'MY_X','album':'qobuz:abc'}
  self.b.playback=Mock(return_value=('PLAYING',{'album_id':'abc'}))
  self.tv.get_current.return_value={'content_id':'MY_X'}
  self.b.step(); self.tv.upload.assert_not_called()
 def test_upload_selection_and_state(self):
  self.b.playback=Mock(return_value=('PLAYING',{'album_id':'abc'}))
  self.tv.get_current.side_effect=[{'content_id':'SAM_A'},{'content_id':'MY_NEW'}]
  self.tv.upload.return_value='MY_NEW'
  self.b.step()
  self.assertEqual(self.b.state['original'],'SAM_A')
  self.assertEqual(self.b.state['owned'],'MY_NEW')
  self.assertEqual(self.b.state['album'],'qobuz:abc')
 def test_stop_restores_and_deletes_owned_only(self):
  self.b.state={'owned':'MY_X','original':'SAM_A','album':'qobuz:abc'}
  self.b.playback=Mock(return_value=('STOPPED',{}))
  self.tv.get_current.side_effect=[{'content_id':'MY_X'},{'content_id':'SAM_A'}]
  self.b.step(); self.tv.select_image.assert_called_once_with('SAM_A'); self.tv.delete.assert_called_once_with('MY_X')
if __name__=='__main__':unittest.main()
