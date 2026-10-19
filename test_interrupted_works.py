import unittest
from classical import Tracks, catalog
from timing import composition


def track(key, title, duration='00:02:00', composer='Example Composer', performer='Example Orchestra'):
    return (f'<div data-track="{key}" data-gtm=\'{{"product":{{"category":"classique"}}}}\'>'
            f'<span class="track__item--name">{title}</span>'
            f'<p class="track__info">{performer}, Orchestra, MainArtist - {composer}, Composer</p>'
            f'<span class="track__item--duration">{duration}</span></div>')


def heading(value='Example Symphony (Example Composer)'):
    return f'<p class="player__work">{value}</p>'


def parse(body):
    p=Tracks();p.feed('<div id="playerTracks">'+body+'</div>')
    return catalog(p.tracks)


def noise(title="Audience's noise - Symphony, Movement 1", duration='00:00:10'):
    return track('noise',title,duration,'na Not Applicable','Audience')


class InterruptedWorksTests(unittest.TestCase):
    def test_numbered_parts_across_audience_noise_without_repeated_heading(self):
        rows=parse(heading()+track('a','I. Lento')+noise()+track('b','II. Allegro'))
        self.assertEqual(composition(rows,'b'),{'index':1,'durations':[120,120]})
        self.assertEqual(rows['b']['work'],'Example Symphony')
        self.assertIsNone(composition(rows,'noise'))
        self.assertNotIn('work',rows['noise'])

    def test_movement_in_heading_and_bare_mosso_tempo(self):
        rows=parse(heading()+track('a','I. Lento')+noise()+
                   heading('Example Symphony, II. By the Water (Example Composer)')+
                   track('b','Andante molto mosso')+heading()+track('c','III. Allegro'))
        self.assertEqual(composition(rows,'b'),{'index':1,'durations':[120]*3})
        self.assertEqual(rows['b']['title'],'Andante molto mosso')
        self.assertEqual(rows['b']['movementName'],'II. By the Water: Andante molto mosso')

    def test_musical_or_long_interruption_never_skipped(self):
        for gap in [noise('Intermezzo'),noise(duration='00:03:00'),track('noise','Applause')]:
            rows=parse(heading()+track('a','I. Lento')+gap+track('b','II. Allegro'))
            self.assertIsNone(composition(rows,'a'))
            self.assertNotIn('non_music_interlude',rows['noise'])

    def test_other_work_disc_composer_or_performer_stops_group(self):
        tails=[heading('Other Symphony (Example Composer)')+track('b','II. Allegro'),
               '<p class="player__work" data-disk="2">Disc 2</p>'+heading()+track('b','II. Allegro'),
               track('b','II. Allegro',composer='Other Composer'),
               track('b','II. Allegro',performer='Other Orchestra')]
        for tail in tails:
            rows=parse(heading()+track('a','I. Lento')+noise()+tail)
            self.assertIsNone(composition(rows,'a'))

    def test_missing_duration_number_gap_and_missing_heading_rejected(self):
        for head,tail in [(heading(),track('b','III. Allegro')),
                          (heading(),track('b','II. Allegro',duration='')),
                          ('',track('b','II. Allegro'))]:
            rows=parse(head+track('a','I. Lento')+noise()+tail)
            self.assertIsNone(composition(rows,'a'))

    def test_repeated_performance_starts_new_scope(self):
        rows=parse(heading()+track('a','I. Lento')+noise()+track('b','II. Allegro')+
                   heading()+track('c','I. Lento')+track('d','II. Allegro'))
        self.assertEqual(composition(rows,'a')['durations'],[120,120])
        self.assertEqual(composition(rows,'c')['durations'],[120,120])
        self.assertNotEqual(rows['a']['work_scope'],rows['c']['work_scope'])

    def test_unrelated_unnumbered_piece_cannot_inherit_heading(self):
        rows=parse(heading()+track('a','I. Lento')+noise()+track('x','Independent Song')+track('b','II. Allegro'))
        self.assertIsNone(composition(rows,'a'))
        self.assertNotIn('work',rows['b'])


if __name__=='__main__':unittest.main()
