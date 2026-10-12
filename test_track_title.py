"""Non-classical current-track captions must not collapse to album identity."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from PIL import Image
from bridge import Bridge
import layouts

class TrackTitleTests(unittest.TestCase):
    def test_same_album_equal_length_tracks_change_display_details(self):
        with tempfile.TemporaryDirectory() as root:
            b=Bridge({},root)
            a={'object_id':'track-a','album':'Example Album','title':'First song','artist':'Artist','duration_seconds':180}
            first=b.details(a)
            second=b.details(dict(a,object_id='track-b',title='Second song'))
            self.assertEqual(first['display_track'],'First song')
            self.assertEqual(second['display_track'],'Second song')
            self.assertNotEqual(first,second)
            # Some controllers reuse an object ID: updated titles still win.
            self.assertEqual(b.details(dict(a,title='Corrected title'))['display_track'],'Corrected title')

    def test_qobuz_track_title_survives_nonclassical_enrichment(self):
        with tempfile.TemporaryDirectory() as root:
            b=Bridge({},root)
            b.album_page=Mock(return_value='<div data-track="2"><span class="track__item--name">Second song</span></div>')
            d=b.details({'album_id':'sample','object_id':'qobuz/track/2','album':'Example Album','title':'Controller title','genre':'Blues'})
            self.assertEqual(d['display_track'],'Second song')
            self.assertNotIn('work',d)

    def test_card_passes_track_without_replacing_album_history(self):
        with tempfile.TemporaryDirectory() as root:
            b=Bridge({},root)
            m={'album_id':'sample','title':'Second song'}
            b.state.update(album='qobuz:sample',history=[{'key':'qobuz:sample','title':'Example Album','artist':'Artist','cover':'covers/sample.jpg'}])
            with patch('bridge.layouts.current') as render:
                _,album=b.card(m,{'display_track':'Second song','duration_seconds':180})
            self.assertEqual(album,'Example Album')
            self.assertEqual(render.call_args.kwargs['track_title'],'Second song')
            self.assertEqual(render.call_args.args[1],'Example Album')

    def test_layout_includes_track_album_artist_and_duration(self):
        with tempfile.TemporaryDirectory() as root:
            cover=Path(root)/'cover.jpg';Image.new('RGB',(50,50),'navy').save(cover)
            layouts.current(cover,'Example Album','Artist',Path(root)/'card.jpg',track_title='Second song',duration_seconds=180)
            draw=Mock();draw.textlength.return_value=100
            with patch('layouts.ImageDraw.Draw',return_value=draw):
                layouts.current(cover,'Example Album','Artist',Path(root)/'card.jpg',track_title='Second song',duration_seconds=180)
            lines=[c.args[1] for c in draw.text.call_args_list]
            self.assertEqual(lines[:3],['Second song','Artist','Example Album'])
            self.assertIn('Track length 3:00',lines[-1])
