import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock
from PIL import Image
import timing
import layouts
from bridge import Bridge, metadata_values
from classical import Tracks


def album(durations=('00:08:00', '00:12:00', '00:04:00')):
    return '<div id="playerTracks"><p class="player__work">Example Symphony</p>' + ''.join(
        f'<div data-track="{i}" data-duration="30"><span class="track__item--name">{i}. Movement</span><span class="track__item--duration">{d}</span></div>'
        for i, d in enumerate(durations, 1)) + '</div>'


class TimingTests(unittest.TestCase):
    def test_lengths_and_unknown_values(self):
        for source, expected in [('00:09:42', 582), ('9:42.2', 582), (125.7, 126), ('3601', 3601)]:
            self.assertEqual(timing.seconds(source), expected)
        for source in [None, True, 0, -1, 'NOT_IMPLEMENTED', '00:99:12', 'inf', float('nan'), {}, 86401]:
            self.assertIsNone(timing.seconds(source))
        self.assertEqual(timing.label(3601), '1:00:01')
        self.assertEqual(timing.label(582), '9:42')

    def test_didl_res_duration(self):
        self.assertEqual(metadata_values('<item><res duration="00:09:42.000">http://example.test/music</res></item>')['duration_seconds'], 582)

    def test_exact_track_and_actual_length_not_sample_length(self):
        with tempfile.TemporaryDirectory() as root:
            b = Bridge({}, root)
            b.album_page = Mock(return_value=album())
            first = b.details({'album_id': 'fixture', 'object_id': 'qobuz/track/2'})
            second = b.details({'album_id': 'fixture', 'object_id': 'qobuz/track/3'})
            self.assertEqual(first['duration_seconds'], 720)
            self.assertEqual(first['composition'], {'index': 1, 'durations': [480, 720, 240]})
            self.assertEqual(second['composition']['index'], 2)
            self.assertNotEqual(first, second)

    def test_full_work_titles_group_without_heading(self):
        with tempfile.TemporaryDirectory() as root:
            b=Bridge({},root)
            b.album_page=Mock(return_value=album().replace('<p class="player__work">Example Symphony</p>', '').replace('1. Movement','Symphony: I. Allegro').replace('2. Movement','Symphony: II. Andante').replace('3. Movement','Symphony: III. Finale'))
            self.assertEqual(b.details({'album_id':'fixture','object_id':'qobuz/track/2'})['composition']['index'],1)

    def test_missing_duration_keeps_track_length_without_strip(self):
        with tempfile.TemporaryDirectory() as root:
            b = Bridge({}, root)
            b.album_page = Mock(return_value=album(('00:08:00', '', '00:04:00')))
            d = b.details({'album_id': 'fixture', 'object_id': 'qobuz/track/1'})
            self.assertEqual(d['duration_seconds'], 480)
            self.assertNotIn('composition', d)

    def test_no_partial_reordered_or_duplicate_work(self):
        for nums in ([2,3], [1,3], [1,2,1,2]):
            tracks = {str(i): {'work':'Example', 'movementNumber':n, 'duration_seconds':60} for i,n in enumerate(nums)}
            self.assertIsNone(timing.composition(tracks, '0'))
        self.assertIsNone(timing.composition({'1':{'title':'Song','duration_seconds':60}}, '1'))

    def test_repeated_work_headings_remain_separate(self):
        p = Tracks()
        p.feed(album() + album().replace('data-track="', 'data-track="x'))
        self.assertEqual(timing.composition(p.tracks, 'x2')['durations'], [480,720,240])
        self.assertEqual(timing.composition(p.tracks, 'x2')['index'], 1)

    def test_late_duration_is_not_frozen_in_cache(self):
        with tempfile.TemporaryDirectory() as root:
            b = Bridge({}, root)
            m = {'object_id':'fixture','title':'Song'}
            self.assertFalse((b.details(m) or {}).get('duration_seconds'))
            self.assertEqual(b.details(dict(m, duration_seconds=123))['duration_seconds'], 123)

    def test_source_lines_preserved_with_duration(self):
        with tempfile.TemporaryDirectory() as root:
            b=Bridge({},root)
            for source in ['roon','sonos']:
                d=b.details({'source':source, source+'_lines':['Track','Artist','Album'], 'duration_seconds':582})
                self.assertEqual(d[source+'_lines'], ['Track','Artist','Album'])
                self.assertEqual(d['duration_seconds'],582)
                self.assertNotIn('composition',d)

    def test_proportional_bars_highlight_and_order(self):
        draw=Mock()
        layouts.track_timing(draw, {'duration_seconds':120,'composition':{'index':1,'durations':[60,120,60]}}, 0,0,1024)
        bars=draw.rectangle.call_args_list
        widths=[call.args[0][2]-call.args[0][0] for call in bars]
        self.assertAlmostEqual(widths[1],widths[0]*2)
        self.assertEqual(bars[1].kwargs['fill'],(237,197,125))
        self.assertNotEqual(bars[0].kwargs['fill'],bars[1].kwargs['fill'])
        self.assertIn('Movement 2 of 3 · 2:00',[c.args[1] for c in draw.text.call_args_list])

    def test_render_all_layouts(self):
        with tempfile.TemporaryDirectory() as root:
            cover=Path(root)/'cover.jpg';Image.new('RGB',(500,500),(55,90,110)).save(cover)
            out=Path(root)/'card.jpg'
            layouts.current(cover,'Album','Artist',out,duration_seconds=582)
            with Image.open(out) as image: self.assertEqual(image.size,(3840,2160))
            layouts.roon(cover,['Track','Artist','Album'],out,duration_seconds=582)
            layouts.classical(cover,{'work':'Example','movement':'II. Andante','duration_seconds':582,'composition':{'index':1,'durations':[600,582,300,720]}},out)
            with Image.open(out) as image: self.assertEqual(image.size,(3840,2160))
