import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from local_composition import build, Catalog, target_file
import timing


def item(disc, track, movement='', album='Example Opera', artist='Example Cast'):
    return {'album':f'{album} {disc}', 'artist':artist, 'track':str(track),
            'duration_seconds':100+track,'movementName':movement,'work':'Example Work','composer':'Composer'}

class LocalCompositionTests(unittest.TestCase):
    def test_no_space_and_prefixed_movement_numbers(self):
        self.assertEqual(timing.number({'movementName':'I.Elegy'}),1)
        self.assertEqual(timing.number({'work':'Quartet','movementName':'Quartet: II.Adagio'}),2)
        self.assertIsNone(timing.number({'movementName':'Isolde'}))

    def test_multidisc_opera_uses_disc_then_track_not_filename_order(self):
        rows={'last':item(2,2,'Final scene'),'first':item(1,1,'Prelude'),
              'middle2':item(2,1,'Next scene'),'middle1':item(1,2,'Aria')}
        result=build(rows,'middle2')
        self.assertEqual(result,{'index':2,'durations':[101,102,101,102],'label':'Part'})

    def test_numbered_work_and_recording_separation(self):
        rows={'a':item(1,7,'I.Elegy'),'b':item(1,8,'II.Serenade'),
              'other':item(1,1,'I.Elegy',artist='Other Cast')}
        self.assertEqual(build(rows,'b')['index'],1)
        self.assertEqual(build(rows,'b')['label'],'Movement')

    def test_ambiguous_missing_or_partial_groups_omit_strip(self):
        for rows in [
            {'a':item(1,1),'b':item(1,1)},
            {'a':item(1,1),'b':item(1,3)},
            {'a':item(1,1,'II.Adagio'),'b':item(1,2,'III.Finale')},
            {'a':item(1,1),'b':dict(item(1,2),duration_seconds=None)},
            {'a':item(1,1),'b':item(3,1)}]:
            self.assertIsNone(build(rows,'a'))

    def test_catalog_only_probes_reviewed_work_and_reuses_results(self):
        with tempfile.TemporaryDirectory() as root:
            root=Path(root);(root/'.listening-room-metadata').mkdir()
            fields={'work':'Quartet','composer':'Composer'}
            data={}
            for name,movement in [('a.flac','I.Elegy'),('b.flac','II.Serenade'),('other.flac','Song')]:
                (root/name).touch();data[name]={'fields':dict(fields,movementName=movement,work='Other' if name=='other.flac' else 'Quartet')}
            (root/'.listening-room-metadata/enriched.json').write_text(json.dumps({'tracks':data}))
            def probe(path):return item(1,1 if path.name=='a.flac' else 2)
            catalog=Catalog()
            with patch('local_composition.probe',side_effect=probe) as p:
                first=catalog.context('http://host/library/a.flac',root,fields,'/library/')
                second=catalog.context('http://host/library/b.flac',root,fields,'/library/')
                self.assertEqual(p.call_count,2)
            self.assertEqual(first['index'],0);self.assertEqual(second['index'],1)
            self.assertIsNone(target_file('http://host/library/../secret.flac',root,'/library/'))

    def test_long_opera_keeps_all_parts(self):
        rows={str(i):item(1,i,'Scene') for i in range(1,25)}
        self.assertEqual(len(build(rows,'18')['durations']),24)
        self.assertEqual(build(rows,'18')['index'],17)
