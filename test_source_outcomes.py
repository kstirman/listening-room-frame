import tempfile,unittest,json
from pathlib import Path
from unittest.mock import Mock,patch
import requests
from bridge import Bridge
import auto_catalog,catalog_pages,source_outcomes
from test_catalog_pages import page,track,response

class OutcomeTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.b=Bridge({'liner_notes_enabled':False},self.tmp.name);self.b.music_catalog.refresh=Mock()
 def error(self,status):
  r=requests.Response();r.status_code=status;r.headers={};return requests.HTTPError(response=r)
 def test_same_track_partial_recovers_after_transient_expiry(self):
  b=self.b;b.session.get=Mock(side_effect=[response(page(51)),requests.Timeout(),response(page(51)),response(track(50))])
  meta={'album_id':'example123','object_id':'qobuz/track/1','duration_seconds':120}
  with patch('bridge.time.time',return_value=1000):
   b.details(meta);b.details(meta)
   self.assertEqual(2,b.session.get.call_count);self.assertEqual(50,len(b.music_catalog.edition('example123')))
   old=b.music_catalog.edition('example123');old['1']['recordingDate']='1980';old['1']['_edition_verified']='example123';b.music_catalog.put_edition('example123',old,None,None)
   before=b.details(meta);self.assertEqual('1980',before['recording_year'])
  with patch('bridge.time.time',return_value=1061):
   after=b.details(meta);self.assertEqual(4,b.session.get.call_count);self.assertEqual(51,len(b.music_catalog.edition('example123')));self.assertEqual('1980',after['recording_year'])
   self.assertTrue(b.music_catalog.edition('example123')['1']['_catalog_complete'])
   b.details(meta);b.details(meta);self.assertEqual(4,b.session.get.call_count)
 def test_denial_survives_restart_without_request(self):
  self.b.session.get=Mock(side_effect=self.error(403))
  with self.assertRaises(requests.HTTPError):self.b.album_page('example123')
  fresh=Bridge({},self.tmp.name);fresh.session.get=Mock(side_effect=AssertionError('denied request'))
  with self.assertRaises(requests.RequestException):fresh.album_page('example123')
  fresh.session.get.assert_not_called();self.assertEqual('denied',fresh.music_catalog.source_outcome('example123')['category'])
 def test_pagination_denial_durable_but_retains_partial(self):
  self.b.session.get=Mock(side_effect=[response(page(51)),self.error(403)])
  html=self.b.album_page('example123');self.assertEqual(50,len(auto_catalog.parsed_rows(html,'example123')))
  self.assertEqual(html,self.b.album_page('example123'));self.assertEqual(2,self.b.session.get.call_count)
  fresh=Bridge({},self.tmp.name);fresh.session.get=Mock()
  with self.assertRaises(requests.RequestException):fresh.album_page('example123')
  fresh.session.get.assert_not_called()
 def test_empty_tail_pending_no_loop_after_time(self):
  self.b.session.get=Mock(side_effect=[response(page(51)),response('')])
  self.b.album_page('example123')
  with patch('bridge.time.time',return_value=10**12):self.b.album_page('example123')
  self.assertEqual(2,self.b.session.get.call_count);self.assertEqual('pending_partial',self.b.source_outcome('example123')['category'])
 def test_three_transient_attempts_stop(self):
  self.b.session.get=Mock(side_effect=requests.Timeout())
  for now in [1000,1061,1182]:
   with patch('bridge.time.time',return_value=now):
    with self.assertRaises(requests.RequestException):self.b.album_page('example123')
  with patch('bridge.time.time',return_value=100000):
   with self.assertRaises(requests.RequestException):self.b.album_page('example123')
  self.assertEqual(3,self.b.session.get.call_count);self.assertEqual('transient_exhausted',self.b.source_outcome('example123')['category'])
 def test_identity_conflict_keeps_reviewed_rows_and_aliases(self):
  old=auto_catalog.parsed_rows(page(51),'example123');old['1']['compositionDate']='1800';old['1']['title']='Reviewed identity'
  self.b.music_catalog.put_edition('example123',old,'Album','Artist');before=self.b.music_catalog.load(('album','artist'))
  self.b.session.get=Mock(side_effect=[response(page(51)),response(track(50))]);self.b.details({'album_id':'example123','object_id':'qobuz/track/1','duration_seconds':120})
  saved=self.b.music_catalog.edition('example123');self.assertEqual('Reviewed identity',saved['1']['title']);self.assertEqual('1800',saved['1']['compositionDate']);self.assertEqual(before,self.b.music_catalog.load(('album','artist')));self.assertEqual('pending_identity_conflict',self.b.source_outcome('example123')['category'])
 def test_no_ordinal_only_merge_or_shrink(self):
  old={'1':{'title':'First','duration_seconds':120,'_catalog_album_id':'abcd','_catalog_track_id':'1','recordingDate':'1980'}}
  for fresh in [{},{'1':dict(old['1'],title='Second')},{'1':dict(old['1'],duration_seconds=121)},{'1':dict(old['1'],_catalog_track_id='2')}]:self.assertIsNone(source_outcomes.merge_refresh(old,fresh,'abcd'))
 def test_retry_after_and_nontransport_parse_pending(self):
  err=self.error(429);err.response.headers['Retry-After']='500';out=source_outcomes.scheduled(source_outcomes.failure(err),None,1000);self.assertEqual(1500,out['next_retry_at'])
  self.assertEqual('pending_parse',source_outcomes.failure(ValueError())['category'])
if __name__=='__main__':unittest.main()
