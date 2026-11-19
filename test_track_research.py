import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock
from bridge import Bridge
from persistent_metadata import Cache
from track_research import Research, recording_match

RID = '12345678-1234-1234-1234-123456789abc'
META = {'album_id':'album123','object_id':'qobuz/track/42','album':'Example','artist':'Artist','title':'Solo','duration_seconds':190}
HIT = {'title':'Solo','id':RID,'artist-credit':[{'name':'Artist'}],'length':190000,'releases':[{'title':'Example'}]}
DETAIL = {'id':RID,'title':'Solo','relations':[
    {'type':'instrument','artist':{'name':'Guest'},'attributes':['viola']},
    {'type':'performance','attributes':['cover'],'work':{'title':'Old theme','relations':[{'type':'composer','artist':{'name':'Writer'}}]}}]}


class RecordingResearchTests(unittest.TestCase):
    def test_named_track_queues_research_and_reuses_after_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg={'recording_research_enabled':True,'musicbrainz_enabled':True,'qobuz_metadata_enabled':False}
            b=Bridge(cfg,tmp)
            b.recording_research.provider.request=Mock(side_effect=[{'count':1,'recordings':[HIT]},DETAIL])
            self.assertEqual(b.details(META)['display_track'],'Solo')
            b.recording_research.queue.join()
            b=Bridge(cfg,tmp)
            b.recording_research.provider.request=Mock(side_effect=AssertionError('offline'))
            notes=b.details(META)['liner_notes']
            self.assertEqual(notes['songwriters'],'Writer')
            self.assertEqual(notes['personnel'],[{'name':'Guest','role':'viola'}])
            self.assertIn('Old theme',notes['note'])
            self.assertEqual(b.recording_research.cache.get('recording-research','album123/qobuz/track/42')['sources'],['https://musicbrainz.org/recording/'+RID])
            b.recording_research.provider.request.assert_not_called()

    def test_similar_or_ambiguous_recording_cannot_supply_context(self):
        for rows in [[dict(HIT,length=170000)],[dict(HIT,releases=[])],[HIT,dict(HIT,id='a'*36)]]:
            self.assertIsNone(recording_match({'count':len(rows),'recordings':rows},META))

    def test_failed_second_source_retains_first_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache=Cache(Path(tmp)/'catalog.sqlite3');mb=Mock();mb.request.side_effect=RuntimeError('offline')
            research=Research(cache,mb)
            get=Mock(return_value=dict(META,producer='Saved producer'))
            research.lookup(META,META,True,True,get);research.queue.join()
            self.assertEqual(research.lookup(META,META,True,True,get)['producer'],'Saved producer')
            get.assert_called_once()

    def test_no_request_if_local_credits_and_context_are_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            research=Research(Cache(Path(tmp)/'db'),Mock())
            self.assertEqual(research.lookup(META,META,False,False),{})
            self.assertIsNone(research.worker)
