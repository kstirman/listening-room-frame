import tempfile,time,unittest
from pathlib import Path
from unittest.mock import Mock
import auto_catalog,catalog_pages,timing
from test_auto_catalog import META,cover,ld


def track(i, appendix=False):
    work='Example Opera / Appendix' if appendix else 'Example Opera / Act 1'
    return f'<div data-track="{i}" data-gtm=\'{{"product":{{"category":"classique"}}}}\'><span class="track__item--name">{work}: Scene {i}</span><p class="track__info">Example Composer, Composer</p><span class="track__item--duration">02:00</span></div>'

def page(count=65):
    return ld({'@type':'Product','name':META['album'],'sku':'example123','image':[cover()]})+f'<div id="playerTracks" data-nbTracks="{count}">'+''.join(track(i) for i in range(50))+'</div><footer>Outside the tracks</footer>'

def response(text):return Mock(text=text,content=text.encode())

class PaginationTests(unittest.TestCase):
    def test_complete_album_keeps_appendix_separate(self):
        get=Mock(return_value=response(''.join(track(i,i>=60) for i in range(50,65))))
        html=catalog_pages.complete(page(),'example123',get)
        rows=auto_catalog.parsed_rows(html)
        self.assertEqual(len(rows),65)
        self.assertIsNone(timing.composition(rows,'60'))
        self.assertEqual(len(timing.composition(rows,'1')['durations']),60)
        self.assertTrue(all(r['_catalog_complete'] for r in rows.values()))
        self.assertEqual(get.call_args.kwargs['params']['offset'],50)
        self.assertEqual(get.call_args.kwargs['headers']['X-Requested-With'],'XMLHttpRequest')
        self.assertLess(html.index('data-track="64"'),html.index('<footer>'))
    def test_missing_or_duplicate_tail_suppresses_whole_work(self):
        for get in [Mock(side_effect=RuntimeError()),Mock(return_value=response(track(0))),Mock(return_value=response(''))]:
            rows=auto_catalog.parsed_rows(catalog_pages.complete(page(),'example123',get))
            self.assertEqual(len(rows),50)
            self.assertIsNone(timing.composition(rows,'1'))
            self.assertFalse(rows['1']['_catalog_complete'])
    def test_multiple_pages_and_failed_later_page(self):
        get=Mock(side_effect=[response(''.join(track(i) for i in range(50,100))),response(''.join(track(i) for i in range(100,115)))])
        rows=auto_catalog.parsed_rows(catalog_pages.complete(page(115),'example123',get))
        self.assertEqual(len(rows),115);self.assertTrue(rows['1']['_catalog_complete'])
        self.assertEqual([c.kwargs['params']['offset'] for c in get.call_args_list],[50,100])
        get=Mock(side_effect=[response(''.join(track(i) for i in range(50,100))),RuntimeError()])
        rows=auto_catalog.parsed_rows(catalog_pages.complete(page(115),'example123',get))
        self.assertEqual(len(rows),100);self.assertIsNone(timing.composition(rows,'1'))
    def test_source_heading_survives_page_boundary(self):
        first='<div id="playerTracks" data-nbTracks="2"><div class="player__work">Example Suite</div>'+track(0).replace('Example Opera / Act 1: Scene 0','I. Allegro')+'</div>'
        tail=track(1).replace('Example Opera / Act 1: Scene 1','II. Andante')
        rows=auto_catalog.parsed_rows(catalog_pages.complete(first,'example123',Mock(return_value=response(tail))))
        self.assertEqual(timing.composition(rows,'1')['index'],1)
    def test_bounds_and_complete_fifty_track_release(self):
        get=Mock()
        for total in [50,1001]:catalog_pages.complete(page(total),'example123',get)
        get.assert_not_called()
        rows=auto_catalog.parsed_rows(page(50));self.assertFalse(catalog_pages.needs_refresh(rows))
        self.assertEqual(len(timing.composition(rows,'1')['durations']),50)
    def test_legacy_fifty_cache_is_refreshed_without_false_total(self):
        with tempfile.TemporaryDirectory() as d:
            resolver=auto_catalog.Resolver(cache_path=Path(d)/'catalog.sqlite3')
            rows=auto_catalog.edition_rows(page(),META['album'],'example123')
            for row in rows.values():row.pop('_catalog_complete');row.pop('_catalog_expected_tracks')
            resolver.disk.put_edition('example123',rows,META['album'],META['artist'])
            html=catalog_pages.complete(page(),'example123',Mock(return_value=response(''.join(track(i,i>=60) for i in range(50,65)))))
            resolver.get=Mock(return_value=html)
            meta=dict(META,albumArtURI=cover(),title=rows['1']['title'],duration_seconds=120)
            self.assertIsNone(resolver.lookup(meta)[0][1])
            deadline=time.monotonic()+3
            while resolver.busy and time.monotonic()<deadline:time.sleep(.01)
            self.assertEqual(len(resolver.lookup(meta)[0][1]['durations']),60)
            resolver.get.assert_called_once()

if __name__=='__main__':unittest.main()
