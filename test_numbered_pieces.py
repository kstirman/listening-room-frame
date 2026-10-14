import tempfile
import unittest
from unittest.mock import Mock

from bridge import Bridge
from classical import Tracks, catalog
from timing import composition, number


def group(work, prefix, numbers):
    return '<p class="player__work">' + work + '</p>' + ''.join(
        f'<div data-track="{prefix}{n}"><span class="track__item--name">'
        f'No. {n} in C Major (Allegro)</span><p class="track__info">'
        f'Example Composer, Composer</p><span class="track__item--duration">'
        f'00:02:{n:02}</span></div>' for n in numbers)


def parse(body):
    parser = Tracks()
    parser.feed('<div id="playerTracks">' + body + '</div>')
    return catalog(parser.tracks)


class NumberedPiecesTests(unittest.TestCase):
    def test_number_formats_and_standalone_titles(self):
        for title in ['No. 2 in C Major', 'No.2: Allegro', 'No. 2, Study', 'II. Slow', '2. Fast']:
            self.assertEqual(number({'title': title}), 2)
        for title in ['No. 2nd Chance', 'Sonata No. 2', '2026 Album']:
            self.assertIsNone(number({'title': title}))

    def test_bridge_retains_work_and_piece_with_separate_opus_groups(self):
        html = '<div id="playerTracks">' + group('Studies, Op. 101', 'a', range(1, 4)) + group('Studies, Op. 102', 'b', range(1, 5)) + '</div>'
        with tempfile.TemporaryDirectory() as root:
            bridge = Bridge({}, root)
            bridge.album_page = Mock(return_value=html)
            first = bridge.details({'album_id': 'fixture', 'object_id': 'qobuz/track/a2'})
            second = bridge.details({'album_id': 'fixture', 'object_id': 'qobuz/track/b3'})
            self.assertEqual(first['work'], 'Studies, Op. 101')
            self.assertEqual(first['movement'], 'No. 2 in C Major (Allegro)')
            self.assertEqual(first['composition'], {'index': 1, 'durations': [121, 122, 123], 'label': 'Part'})
            self.assertEqual(second['composition'], {'index': 2, 'durations': [121, 122, 123, 124], 'label': 'Part'})

    def test_repeated_headings_and_long_numbered_sets(self):
        rows = parse(group('Studies', 'a', range(1, 25)) + group('Studies', 'b', range(1, 4)))
        self.assertEqual(len(composition(rows, 'a20')['durations']), 24)
        self.assertEqual(composition(rows, 'b2')['durations'], [121, 122, 123])

    def test_gaps_missing_headings_and_disc_boundaries_do_not_group(self):
        for nums in ([2, 3], [1, 3]):
            self.assertIsNone(composition(parse(group('Studies', 'a', nums)), 'a3'))
        body = group('Studies', 'a', range(1, 4)).replace('<p class="player__work">Studies</p>', '')
        self.assertIsNone(composition(parse(body), 'a2'))
        rows = parse(group('Studies', 'a', [1]) + '<p class="player__work" data-disk="2">Disc 2</p>' + body)
        self.assertNotIn('work', rows['a2'])
