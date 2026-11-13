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
 def test_catalog_outage_uses_current_metadata_and_retries(self):
  import requests
  self.b.album_page=Mock(side_effect=requests.HTTPError('blocked'))
  metadata={'object_id':'qobuz/track/1','album_id':'abc','title':'New song','duration_seconds':200}
  result=Bridge.details(self.b,metadata)
  self.assertEqual(result['display_track'],'New song')
  self.assertEqual(result['duration_seconds'],200)
  self.assertFalse(self.b.detail_cache)
  self.b.album_page.side_effect=None
  self.b.album_page.return_value='<script type="application/ld+json">{"@type":"MusicAlbum","datePublished":"2012"}</script>'
  self.assertEqual(Bridge.details(self.b,metadata)['release_year'],'2012')
 def test_catalog_outage_preserves_saved_rows(self):
  import requests
  self.b.music_catalog.edition=Mock(return_value={'1':{'title':'Saved song','duration_seconds':200}})
  self.b.album_page=Mock(side_effect=requests.HTTPError('blocked'))
  with patch('catalog_pages.needs_refresh',return_value=True):
   result=Bridge.details(self.b,{'object_id':'qobuz/track/1','album_id':'abc'})
  self.assertEqual(result['display_track'],'Saved song')
 def test_album_page_failure_backoff_and_recovery(self):
  import requests
  self.b.session=Mock()
  self.b.session.get.return_value.raise_for_status.side_effect=requests.Timeout('transient')
  with patch('bridge.time.time',return_value=10):
   for _ in range(2):
    with self.assertRaises(requests.RequestException):self.b.album_page('abc')
  self.assertEqual(self.b.session.get.call_count,1)
  self.b.session.get.return_value.raise_for_status.side_effect=None
  self.b.session.get.return_value.text='recovered'
  with patch('bridge.time.time',return_value=71),patch('catalog_pages.complete_result',return_value=('recovered', {'category':'complete'})):
   self.assertEqual(self.b.album_page('abc'),'recovered')
  self.assertEqual(self.b.session.get.call_count,2)
 def test_qobuz_cover_uses_validated_renderer_cover_on_outage(self):
  import io,requests
  from PIL import Image
  self.b.album_page=Mock(side_effect=requests.HTTPError('blocked'))
  data=io.BytesIO();Image.new('RGB',(32,32),'red').save(data,format='JPEG')
  self.b.session=Mock();self.b.session.get.return_value.content=data.getvalue()
  url='https://static.qobuz.com/images/covers/ab/cd.jpg'
  with patch('bridge.layouts.current'):
   _,title=Bridge.cover(self.b,{'album_id':'abc','album':'New album','artist':'New artist','albumArtURI':url})
  self.assertEqual(title,'New album');self.b.session.get.assert_called_once_with(url,timeout=15)
  self.assertFalse(Bridge.qobuz_cover_url('https://secret@static.qobuz.com/images/covers/a.jpg'))
 def test_sparse_reviewed_qobuz_captions_and_cover_without_page(self):
  import io
  from PIL import Image
  url='https://static.qobuz.com/images/covers/verified.jpg'
  row={'title':'Current song','album':'Current soundtrack','albumArtist':'Soundtrack band','artist':'Track singer','duration_seconds':200,'_catalog_album_id':'abc','_edition_verified':'abc','_album_art_url':url}
  self.b.music_catalog.edition=Mock(return_value={'1':row})
  self.b.album_page=Mock(side_effect=AssertionError('saved edition needs no page'))
  metadata={'object_id':'qobuz/track/1','album_id':'abc','duration_seconds':200}
  with patch('catalog_pages.needs_refresh',return_value=False):
   details=Bridge.details(self.b,metadata)
  self.assertEqual(details['display_album'],'Current soundtrack')
  self.assertEqual(details['display_artist'],'Soundtrack band')
  data=io.BytesIO();Image.new('RGB',(32,32),'red').save(data,format='JPEG')
  self.b.session=Mock();self.b.session.get.return_value.content=data.getvalue()
  with patch('bridge.layouts.current'):
   _,title=Bridge.cover(self.b,metadata)
  self.assertEqual(title,'Current soundtrack');self.assertEqual(self.b.state['artist'],'Soundtrack band')
  self.b.session.get.assert_called_once_with(url,timeout=15);self.b.album_page.assert_not_called()
 def test_reviewed_cover_rejects_other_edition_and_unsafe_url(self):
  import requests
  from bridge import ArtworkUnavailable
  self.b.album_page=Mock(side_effect=requests.HTTPError('blocked'))
  metadata={'object_id':'qobuz/track/1','album_id':'abc'}
  for row in [
   {'_catalog_album_id':'other','_edition_verified':'abc','_album_art_url':'https://static.qobuz.com/images/covers/other.jpg'},
   {'_catalog_album_id':'abc','_edition_verified':'other','_album_art_url':'https://static.qobuz.com/images/covers/other.jpg'},
   {'_catalog_album_id':'abc','_edition_verified':'abc','_album_art_url':'https://evil.example/cover.jpg'}]:
   self.b.music_catalog.edition=Mock(return_value={'1':row})
   with self.assertRaises(ArtworkUnavailable):Bridge.cover(self.b,metadata)
  self.b.music_catalog.edition=Mock(return_value={'2':{'_catalog_album_id':'abc','_edition_verified':'abc'}})
  self.assertEqual(self.b.reviewed_edition_row(metadata),{})
 def test_sparse_cover_does_not_reuse_other_albums_history(self):
  import requests
  from bridge import ArtworkUnavailable
  self.b.album_page=Mock(side_effect=requests.HTTPError('blocked'))
  old=self.b.root/'covers/old.jpg';old.write_bytes(b'old')
  self.b.state['history']=[{'key':'qobuz:old','cover':'covers/old.jpg','title':'Wrong album'}]
  with self.assertRaises(ArtworkUnavailable):
   Bridge.cover(self.b,{'album_id':'new'})
 def test_sparse_album_cdn_candidate_renders_without_invented_caption(self):
  import io,requests
  from PIL import Image
  self.b.album_page=Mock(side_effect=requests.HTTPError('blocked'))
  data=io.BytesIO();Image.new('RGB',(32,32),'red').save(data,format='JPEG')
  self.b.session=Mock();response=self.b.session.get.return_value
  response.content=data.getvalue();response.status_code=200;response.headers={'Content-Type':'image/jpeg'}
  with patch('bridge.layouts.current') as render:
   _,title=Bridge.cover(self.b,{'album_id':'examplealbum09ab'})
  self.assertEqual(title,'');self.assertEqual(self.b.state['artist'],'')
  self.b.session.get.assert_called_once_with('https://static.qobuz.com/images/covers/ab/09/examplealbum09ab_600.jpg',timeout=15,allow_redirects=False)
  self.assertEqual(render.call_args.args[1:3],('',''))
  self.assertEqual(self.b.state['history'][0]['key'],'qobuz:examplealbum09ab')
 def test_sparse_album_cdn_candidate_failure_does_not_save_history(self):
  import requests
  from bridge import ArtworkUnavailable
  self.b.album_page=Mock(side_effect=requests.HTTPError('blocked'))
  self.b.session=Mock();response=self.b.session.get.return_value
  for status,mime,content in [(404,'image/jpeg',b'missing'),(302,'image/jpeg',b'redirect'),(200,'text/html',b'blocked'),(200,'image/jpeg',b'invalid image')]:
   response.status_code=status;response.headers={'Content-Type':mime};response.content=content
   with self.assertRaises(ArtworkUnavailable):Bridge.cover(self.b,{'album_id':'examplealbum09ab'})
   self.assertEqual(self.b.state['history'],[])
  response.raise_for_status.side_effect=requests.Timeout('timeout')
  with self.assertRaises(ArtworkUnavailable):Bridge.cover(self.b,{'album_id':'examplealbum09ab'})
 def test_sparse_album_cdn_candidate_rejects_untrusted_id(self):
  import requests
  from bridge import ArtworkUnavailable
  self.b.album_page=Mock(side_effect=requests.HTTPError('blocked'));self.b.session=Mock()
  for album in ['../other','https://evil.example/a','abcd?token=x']:
   with self.assertRaises(ArtworkUnavailable):Bridge.cover(self.b,{'album_id':album})
  self.b.session.get.assert_not_called()
 def test_sparse_ordinal_edition_matches_unique_duration_and_work(self):
  rows={str(i):{'title':f'Part {i}','album':'Quartet album','albumArtist':'Ensemble',
   'duration_seconds':d,'_catalog_album_id':'abc','_edition_verified':'abc',
   '_catalog_complete':True,'_catalog_expected_tracks':2,'_source_ordinal':i,
   'work':'Quartet','composer':'Composer','composition_group':'abc-quartet',
   'composition_index':i,'composition_count':2} for i,d in enumerate([723,732],1)}
  self.b.music_catalog.edition=Mock(return_value=rows)
  self.b.album_page=Mock(side_effect=AssertionError('complete saved edition'))
  metadata={'album_id':'abc','object_id':'qobuz/track/987654','duration_seconds':723}
  result=Bridge.details(self.b,metadata)
  self.assertEqual(result['display_track'],'Part 1')
  self.assertEqual(result['display_album'],'Quartet album')
  self.assertEqual(result['composition'],__import__('timing').composition(rows,'1'))
  self.assertIsNotNone(result['composition'])
  self.assertEqual(self.b.reviewed_edition_match(metadata)[0],'1')
  self.b.album_page.assert_not_called()
 def test_ordinal_matching_rejects_ambiguity_partial_mismatch_and_other_edition(self):
  base={'title':'Part','album':'Album','albumArtist':'Ensemble','duration_seconds':200,
   '_catalog_album_id':'abc','_edition_verified':'abc','_catalog_complete':True,'_catalog_expected_tracks':2}
  rows={'1':dict(base,_source_ordinal=1),'2':dict(base,_source_ordinal=2,duration_seconds=201)}
  self.b.music_catalog.edition=Mock(return_value=rows)
  metadata={'album_id':'abc','object_id':'qobuz/track/999','duration_seconds':200}
  self.assertTrue(self.b.reviewed_edition_row(metadata))
  for changed in [dict(metadata,duration_seconds=202),dict(metadata,title='Different'),dict(metadata,album_id='other')]:
   self.assertEqual(self.b.reviewed_edition_row(changed),{})
  rows['2']['duration_seconds']=200
  self.assertEqual(self.b.reviewed_edition_row(metadata),{})
  self.assertEqual(self.b.reviewed_edition_album(metadata)['album'],'Album')
  rows.pop('2')
  self.assertEqual(self.b.reviewed_edition_row(metadata),{})
 def test_album_cover_survives_unknown_track_but_conflicting_album_fields_do_not(self):
  import io
  from PIL import Image
  url='https://static.qobuz.com/images/covers/verified.jpg'
  row={'album':'Album','albumArtist':'Ensemble','_album_art_url':url,
       '_catalog_album_id':'abc','_edition_verified':'abc'}
  rows={'1':dict(row),'2':dict(row)}
  self.b.music_catalog.edition=Mock(return_value=rows)
  self.b.album_page=Mock(side_effect=AssertionError('saved album cover'))
  data=io.BytesIO();Image.new('RGB',(32,32),'red').save(data,format='JPEG')
  self.b.session=Mock();self.b.session.get.return_value.content=data.getvalue()
  metadata={'album_id':'abc','object_id':'qobuz/track/999'}
  with patch('bridge.layouts.current'):
   _,title=Bridge.cover(self.b,metadata)
  self.assertEqual(title,'Album')
  self.b.session.get.assert_called_once_with(url,timeout=15)
  rows['2']['album']='Conflicting album'
  self.assertNotIn('album',self.b.reviewed_edition_album(metadata))
 def test_exact_saved_cover_survives_page_outage(self):
  import requests
  self.b.album_page=Mock(side_effect=requests.HTTPError('blocked'))
  saved=self.b.root/'covers/right.jpg';saved.write_bytes(b'original')
  self.b.state['history']=[{'key':'qobuz:abc','cover':'covers/right.jpg','title':'Right album','artist':'Artist'}]
  with patch('bridge.layouts.current') as draw:
   image,title=Bridge.cover(self.b,{'album_id':'abc'})
  self.assertEqual(title,'Right album');self.assertNotEqual(image,saved)
  self.assertEqual(saved.read_bytes(),b'original')
  self.assertEqual(draw.call_args.args[0],saved)
 def test_sparse_new_album_outage_restores_unrelated_art(self):
  from bridge import ArtworkUnavailable
  metadata={'object_id':'qobuz/track/1','album_id':'abc','duration_seconds':200}
  self.b.playback=Mock(return_value=('PLAYING',metadata))
  self.b.state.update(owned='MY_OLD',album='qobuz:old',original='SAM_A')
  self.tv.get_current.return_value={'content_id':'MY_OLD'}
  self.b.card=Mock(side_effect=ArtworkUnavailable('no cover'))
  self.b.restore=Mock()
  self.b.step()
  self.b.restore.assert_called_once();self.tv.upload.assert_not_called()
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
