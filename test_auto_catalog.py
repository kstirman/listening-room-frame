import json
from pathlib import Path
import sqlite3
import threading
import tempfile
import time
import unittest
from unittest.mock import Mock, patch
from bridge import Bridge
from classical import normalize
from auto_catalog import Resolver, candidates, album_rows, MAX_CANDIDATES, CACHE_AGE


def ld(data):
    return '<script type="application/ld+json">'+json.dumps(data)+'</script>'


def search(ids=('abc',)):
    return ld({'@type':'ItemList','itemListElement':[{'item':{'@type':'MusicAlbum','name':'Example Cycle','url':'https://www.qobuz.com/us-en/album/example/'+i}} for i in ids]})


def page(artist='Example Choir', missing=False):
    return ld({'@type':'Product','name':'Example Cycle','brand':{'name':artist}})+'<div id="playerTracks">'+''.join(
        f'<div data-track="{i}" data-gtm=\'{{"product":{{"category":"classique"}}}}\'><span class="track__item--name">Cycle: No. {i}, Part</span><p class="track__info">Example Composer, Composer</p><span class="track__item--duration">{length}</span></div>'
        for i,length in [(1,'00:02:00'),(2,'' if missing else '00:03:00')])+'</div>'


META={'album':'Example Cycle','artist':'Example Choir','title':'Cycle: No. 2, Part','genre':'Classical','duration_seconds':180,'resource':'http://example.test/audirvana/audio_f_13.wav'}


class AutomaticCatalogTests(unittest.TestCase):
    def wait(self, resolver):
        deadline=time.monotonic()+2
        while resolver.busy and time.monotonic()<deadline:
            time.sleep(.005)
        self.assertFalse(resolver.busy)

    def test_background_match_reuses_full_album_for_next_track(self):
        get=Mock(side_effect=[search(),page()]);r=Resolver(get)
        self.assertEqual(r.lookup(META)[1],'searching');self.wait(r)
        match,status=r.lookup(META)
        self.assertEqual(status,'matched');self.assertEqual(match[1]['index'],1)
        match,status=r.lookup(dict(META,title='Cycle: No. 1, Part',duration_seconds=120))
        self.assertEqual(match[1]['index'],0);self.assertEqual(get.call_count,2)

    def test_persistent_catalog_survives_restart_and_still_checks_recording(self):
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/'catalog-cache.sqlite3'
            first=Resolver(Mock(side_effect=[search(),page()]),cache_path=path)
            first.lookup(META);self.wait(first)
            offline=Mock(side_effect=AssertionError('Saved album must not need network'))
            restarted=Resolver(offline,cache_path=path)
            self.assertEqual(restarted.lookup(META)[1],'matched')
            self.assertEqual(restarted.lookup(dict(META,duration_seconds=900))[1],'track_mismatch')
            self.assertEqual(restarted.lookup(dict(META,title='Cycle: No. 1, Part',duration_seconds=120))[0][1]['index'],0)
            offline.assert_not_called()
            self.assertEqual(path.stat().st_mode & 0o777,0o600)

    def test_persisted_ambiguity_is_not_promoted_to_unique_match(self):
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/'catalog-cache.sqlite3'
            first=Resolver(Mock(side_effect=[search(('a','b')),page(),page()]),cache_path=path)
            first.lookup(META);self.wait(first)
            self.assertEqual(Resolver(Mock(),cache_path=path).lookup(META)[1],'ambiguous')

    def test_old_catalog_is_immediate_and_failed_refresh_preserves_it(self):
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/'catalog-cache.sqlite3'
            first=Resolver(Mock(side_effect=[search(),page()]),cache_path=path)
            first.lookup(META);self.wait(first)
            with sqlite3.connect(path) as db:db.execute('UPDATE catalogs SET saved_at=?',(time.time()-CACHE_AGE-1,))
            gate=threading.Event()
            def unavailable(*args, **kwargs):
                gate.wait(2)
                raise RuntimeError('Temporary catalog outage')
            restarted=Resolver(unavailable,cache_path=path)
            self.assertEqual(restarted.lookup(META)[1],'matched')
            self.assertTrue(restarted.busy)
            gate.set();self.wait(restarted)
            self.assertEqual(restarted.lookup(META)[1],'matched')
            self.assertEqual(restarted.lookup(dict(META,duration_seconds=900))[1],'track_mismatch')

    def test_corrupt_and_failed_cache_do_not_block_fresh_discovery(self):
        for scenario in ('corrupt','failure'):
            with self.subTest(scenario=scenario),tempfile.TemporaryDirectory() as root:
                path=Path(root)/'catalog-cache.sqlite3'
                first=Resolver(Mock(side_effect=[search(),page()]),cache_path=path)
                first.lookup(META);self.wait(first)
                if scenario=='corrupt':path.write_text('invalid sqlite')
                else:
                    path.unlink()
                    failed=Resolver(Mock(side_effect=RuntimeError()),cache_path=path)
                    failed.lookup(META);self.wait(failed)
                    self.assertFalse(path.exists())
                fresh=Resolver(Mock(side_effect=[search(),page()]),cache_path=path)
                self.assertEqual(fresh.lookup(META)[1],'searching');self.wait(fresh)
                self.assertEqual(fresh.lookup(META)[1],'matched')

    def test_catalog_completion_refreshes_details_within_same_retry_bucket(self):
        with tempfile.TemporaryDirectory() as root:
            gate=threading.Event()
            def get(path, params=None):
                self.assertTrue(gate.wait(2))
                return search() if path=='search' else page()
            b=Bridge({},root);b.auto_catalog=Resolver(get)
            with patch('bridge.time.monotonic',return_value=100):
                self.assertEqual(b.details(META)['catalog_status'],'searching')
            gate.set()
            self.wait(b.auto_catalog)
            with patch('bridge.time.monotonic',return_value=100):
                self.assertEqual(b.details(META)['catalog_status'],'matched')

    def test_artist_and_editions_checked(self):
        for pages,status in [([page('Other')],'unmatched'),([page(),page()],'ambiguous')]:
            r=Resolver(Mock(side_effect=[search(('a','b') if len(pages)==2 else ('a',))]+pages+([search(())] if status=='unmatched' else [])))
            r.lookup(META);self.wait(r);self.assertEqual(r.lookup(META)[1],status)

    def test_wrong_duration_and_missing_parts(self):
        for p,meta,status in [(page(),dict(META,duration_seconds=200),'track_mismatch'),
                              (page(missing=True),dict(META,title='Cycle: No. 1, Part',duration_seconds=120),'matched_track')]:
            r=Resolver(Mock(side_effect=[search(),p]));r.lookup(meta);self.wait(r)
            self.assertEqual(r.lookup(meta)[1],status)

    def test_unique_standalone_track_keeps_credits_without_a_movement_strip(self):
        p=page().replace('Cycle: No. 1, Part','Standalone Tone Poem')
        p=p.replace('Example Composer, Composer','Example Composer, Composer - Example Conductor, Conductor - Example Orchestra, Orchestra')
        meta=dict(META,title='Standalone Tone Poem',duration_seconds=120)
        resolver=Resolver(Mock(side_effect=[search(),p]));resolver.lookup(meta);self.wait(resolver)
        match,status=resolver.lookup(meta)
        self.assertEqual(status,'matched_track');self.assertIsNone(match[1])
        with tempfile.TemporaryDirectory() as root:
            b=Bridge({},root);b.auto_catalog=resolver
            d=b.details(meta)
            self.assertEqual(d['conductor'],'Example Conductor')
            self.assertEqual(d['orchestra'],'Example Orchestra')
            self.assertNotIn('composition',d)

    def test_failure_negative_cache_and_candidate_limit(self):
        for get,status in [(Mock(side_effect=RuntimeError()),'unavailable'),
                           (Mock(return_value=search(tuple(str(i) for i in range(MAX_CANDIDATES+1)))),'ambiguous')]:
            r=Resolver(get);r.lookup(META);self.wait(r)
            self.assertEqual(r.lookup(META)[1],status);r.lookup(META)
            self.assertEqual(get.call_count,1)
        self.assertEqual(candidates(search().replace('www.qobuz.com','evil.test'),'Example Cycle'),[])
        self.assertIsNone(album_rows(page(),'Different','Example Choir'))

    def test_many_same_title_releases_still_require_unique_track_match(self):
        wrong=page().replace('00:03:00','00:04:00')
        for valid,status in [(1,'matched'),(2,'ambiguous')]:
            get=Mock(side_effect=[search(tuple(str(i) for i in range(6)))]+[wrong]*(6-valid)+[page()]*valid)
            resolver=Resolver(get);resolver.lookup(META);self.wait(resolver)
            match,actual=resolver.lookup(META)
            self.assertEqual(actual,status)
            self.assertEqual(get.call_count,7)
            if valid==1:self.assertEqual(match[1]['durations'],[120,180])

    def test_fallback_known_part_without_invented_total(self):
        d=normalize(META)
        self.assertEqual(d['work'],'Cycle');self.assertEqual(d['movement'],'No. 2, Part')
        self.assertNotIn('composition',d)
        self.assertIsNone(normalize(dict(META,genre='Pop')))

    def test_bridge_opt_out_and_source_independent_opt_in(self):
        with tempfile.TemporaryDirectory() as root:
            b=Bridge({'auto_catalog':False},root);b.auto_catalog.lookup=Mock()
            self.assertEqual(b.details(META)['movement'],'No. 2, Part')
            b.auto_catalog.lookup.assert_not_called()
        with tempfile.TemporaryDirectory() as root:
            b=Bridge({'auto_catalog_other_upnp':True},root)
            b.auto_catalog.lookup=Mock(return_value=(None,'ambiguous'))
            d=b.details(dict(META,resource='http://example.test/proxy/1'))
            self.assertEqual(d['catalog_status'],'ambiguous')

    def test_artist_refinement_when_generic_title_hides_recording(self):
        get=Mock(side_effect=[search(('other',)),page('Other'),search(('target',)),page()])
        r=Resolver(get);r.lookup(META);self.wait(r)
        self.assertEqual(r.lookup(META)[1],'matched')
        self.assertEqual(get.call_args_list[2].args[1]['q'],'Example Cycle Example Choir')
        self.assertEqual(get.call_count,4)

    def test_refined_search_keeps_artist_and_duration_guards(self):
        get=Mock(side_effect=[search(()),search(('target',)),page('Other')])
        r=Resolver(get);r.lookup(META);self.wait(r)
        self.assertEqual(r.lookup(META)[1],'unmatched')
