import unittest
import classical,timing

def rows(titles=None):
    titles=titles or ['Introduction','Theme: The Traveller','Variation I (Setting out)','Variation II (A battle)','Finale (Home again)']
    return {str(i):dict(title=t,catalog_heading='Example Variations, Op. 8 (Example Composer)',catalog_disc=0,genre='Classical',credits='Example Composer, Composer - Example Conductor, Conductor',duration_seconds=60) for i,t in enumerate(titles)}

class VariationCycleTests(unittest.TestCase):
    def test_complete_cycle_and_next_work(self):
        source=rows();source['5']=dict(title='I. Allegro',work='Other Work',catalog_heading='Other Work',duration_seconds=100)
        result=classical.catalog(source);context=timing.composition(result,'3')
        self.assertEqual(context,{'index':3,'durations':[60]*5,'label':'Part'})
        self.assertEqual(result['3']['work'],'Example Variations, Op. 8')
        self.assertIsNone(timing.composition(result,'5'))
        self.assertEqual(classical.catalog(result),result)
    def test_no_intro_or_finale_required(self):
        result=classical.catalog(rows(['Theme','Variation 1: Slow','Variation 2: Fast']))
        self.assertEqual(len(timing.composition(result,'1')['durations']),3)
    def test_missing_repeated_or_foreign_tracks_rejected(self):
        for titles in [['Theme','Variation I','Variation III'],['Theme','Variation I','Variation II','Variation I'],['Theme','Variation I','Unrelated song','Variation II'],['Introduction','Variation I','Variation II']]:
            result=classical.catalog(rows(titles));self.assertFalse(any(timing.composition(result,k) for k in result))
    def test_requires_source_heading_credit_identity_and_lengths(self):
        for change in [dict(catalog_heading='Different work'),dict(credits='Other Composer, Composer - Example Conductor, Conductor'),dict(duration_seconds=None),dict(catalog_disc=1)]:
            source=rows();source['2'].update(change);result=classical.catalog(source)
            self.assertFalse(any(timing.composition(result,k) for k in result))
    def test_unheaded_tracks_cannot_inherit_album_name(self):
        source=rows()
        for r in source.values():r.pop('catalog_heading')
        result=classical.catalog(source);self.assertFalse(any(timing.composition(result,k) for k in result))

if __name__=='__main__':unittest.main()
