import tempfile
import unittest
from unittest.mock import Mock
from bridge import Bridge
from classical import catalog
from timing import composition


def rows(names, genre='classique', composer='Example Composer'):
    return {str(i):{'title':name,'genre':genre,'credits':composer+', Composer',
                    'duration_seconds':100+i} for i,name in enumerate(names)}

class NamedPartsTests(unittest.TestCase):
    def test_unnumbered_sections_are_one_work_in_album_order(self):
        tracks=catalog(rows(['Sonata, Op. 123: Lento','Sonata, Op. 123: Andante',
                             'Sonata, Op. 123: Allegro','Sonata, Op. 123: Finale']))
        self.assertEqual(composition(tracks,'2'),{'index':2,'durations':[100,101,102,103],'label':'Part'})
        self.assertEqual(tracks['2']['work'],'Sonata, Op. 123')
        self.assertEqual(tracks['2']['movementName'],'Allegro')

    def test_standalone_colon_pop_titles_and_different_composers_do_not_group(self):
        self.assertIsNone(composition(catalog(rows(['Work: One'])),'0'))
        self.assertIsNone(composition(catalog(rows(['Song: One','Song: Two'],genre='Blues')),'0'))
        data=rows(['Work: One','Work: Two']);data['1']['credits']='Other, Composer'
        self.assertIsNone(composition(catalog(data),'0'))

    def test_repeated_recordings_and_numbered_gaps_stay_ambiguous(self):
        for names in [['Work: Lento','Work: Fast','Work: Lento','Work: Fast'],
                      ['Work: I. Lento','Work: III. Fast']]:
            self.assertIsNone(composition(catalog(rows(names)),'0'))

    def test_adjacent_different_works_stay_separate(self):
        data=catalog(rows(['A: Slow','A: Fast','B: Slow','B: Fast']))
        self.assertEqual(composition(data,'2')['durations'],[102,103])
        self.assertEqual(composition(data,'2')['index'],0)

    def test_bridge_classical_card_gets_split_title_and_part_context(self):
        html='<div id="playerTracks">'+''.join(f'<div data-track="{i}" data-gtm=\'{{"product":{{"category":"classique"}}}}\'><span class="track__item--name">Sonata: {name}</span><p class="track__info">Example Composer, Composer</p><span class="track__item--duration">00:02:00</span></div>' for i,name in enumerate(['Lento','Andante','Allegro']))+'</div>'
        with tempfile.TemporaryDirectory() as root:
            b=Bridge({},root);b.album_page=Mock(return_value=html)
            d=b.details({'album_id':'fixture','object_id':'qobuz/track/1'})
            self.assertEqual(d['work'],'Sonata');self.assertEqual(d['movement'],'Andante')
            self.assertEqual(d['composition'],{'index':1,'durations':[120,120,120],'label':'Part'})
