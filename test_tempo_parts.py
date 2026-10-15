import tempfile
import unittest
from unittest.mock import Mock
from bridge import Bridge
from classical import Tracks, catalog
from timing import composition


def track(key, title, duration='00:03:00'):
    return f'<div data-track="{key}" data-gtm=\'{{"product":{{"category":"classique"}}}}\'><span class="track__item--name">{title}</span><p class="track__info">Example Composer, Composer</p><span class="track__item--duration">{duration}</span></div>'


def parse(body):
    p = Tracks()
    p.feed('<div id="playerTracks">' + body + '</div>')
    return catalog(p.tracks)


class TempoPartsTests(unittest.TestCase):
    def test_bridge_groups_bare_tempos_under_explicit_heading(self):
        titles = ['Lento assai - Allegro energico - Grandioso - Recitativo',
                  'Andante sostenuto -', 'Allegro energico', 'Andante sostenuto - Lento assai']
        html = '<div id="playerTracks"><p class="player__work">Example Sonata</p>' + ''.join(track(str(i), t) for i, t in enumerate(titles)) + '</div>'
        with tempfile.TemporaryDirectory() as root:
            b = Bridge({}, root)
            b.album_page = Mock(return_value=html)
            d = b.details({'album_id': 'fixture', 'object_id': 'qobuz/track/1'})
            self.assertEqual(d['work'], 'Example Sonata')
            self.assertEqual(d['movement'], titles[1])
            self.assertEqual(d['composition'], {'index': 1, 'durations': [180]*4, 'label': 'Part'})

    def test_standalone_title_ends_heading_inheritance(self):
        rows = parse('<p class="player__work">Sonata</p>' + track('1', 'Lento') + track('2', 'Allegro') + track('3', 'Berceuse, Op. 99') + track('4', 'Andante'))
        self.assertEqual(len(composition(rows, '1')['durations']), 2)
        self.assertNotIn('work', rows['3'])
        self.assertNotIn('work', rows['4'])

    def test_absent_heading_missing_length_and_duplicate_sections(self):
        self.assertIsNone(composition(parse(track('1', 'Lento') + track('2', 'Allegro')), '1'))
        for tail in [track('2', 'Allegro', ''), track('2', 'Lento')]:
            self.assertIsNone(composition(parse('<p class="player__work">Sonata</p>' + track('1', 'Lento') + tail), '1'))

    def test_new_heading_and_disc_boundary_do_not_leak(self):
        body = '<p class="player__work">A</p>' + track('1', 'Lento') + track('2', 'Allegro')
        rows = parse(body + '<p class="player__work">B</p>' + track('3', 'Andante') + track('4', 'Presto') + '<p class="player__work" data-disk="2">Disc 2</p>' + track('5', 'Adagio'))
        self.assertEqual(composition(rows, '3')['index'], 0)
        self.assertEqual(rows['3']['work'], 'B')
        self.assertNotIn('work', rows['5'])
