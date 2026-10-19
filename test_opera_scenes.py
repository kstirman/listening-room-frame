import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
import classical
from music_catalog import Store
import timing


def rows():
    titles = ['Composer: Opera One, Op. 10: Prelude',
              'Composer: Opera One, Op. 10 / Scene I: Opening',
              'Composer: Opera One, Op. 10 / Scene I: Answer',
              'Composer: Opera One, Op. 10 / Scene II: Finale']
    return {str(i):dict(title=t,composer='Example Composer',genre='Classical',
                       duration_seconds=100+i,_catalog_album_id='example')
            for i,t in enumerate(titles)}


class OperaScenesTests(unittest.TestCase):
    def test_prelude_and_scenes_share_whole_work(self):
        result=classical.catalog(rows())
        for i in range(4):
            context=timing.composition(result,str(i))
            self.assertEqual(context,dict(index=i,durations=[100,101,102,103],label='Part'))
        self.assertEqual(result['1']['movementName'],'Scene I: Opening')
        self.assertEqual(result['0']['work'],'Opera One, Op. 10')

    def test_other_work_and_repeated_performance_not_merged(self):
        source=rows();source['4']=dict(source['0'],title='Composer: Opera Two: Prelude')
        result=classical.catalog(source)
        self.assertEqual(len(timing.composition(result,'0')['durations']),4)
        self.assertIsNone(timing.composition(result,'4'))
        source=rows();source.update({str(int(k)+4):v for k,v in rows().items()})
        self.assertIsNone(timing.composition(classical.catalog(source),'0'))

    def test_composer_and_explicit_scope_boundaries_retained(self):
        for field,value in [('composer','Another Composer'),('work_scope','another performance')]:
            source=rows();source['3'][field]=value
            result=classical.catalog(source)
            self.assertEqual(len(timing.composition(result,'0')['durations']),3)
            self.assertIsNone(timing.composition(result,'3'))

    def test_saved_rows_receive_current_rules_without_network_or_rewrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            store=Store(Path(tmp)/'music.db');source=rows()
            store.save(('example album','example artist'),[source])
            with sqlite3.connect(store.path) as db:
                raw=db.execute('select payload from catalogs').fetchone()[0]
            for saved in [store.load(('example album','example artist'))[0][0],store.edition('example')]:
                self.assertEqual(timing.composition(saved,'0')['index'],0)
                self.assertEqual(classical.catalog(saved),saved)
            with sqlite3.connect(store.path) as db:
                self.assertEqual(db.execute('select payload from catalogs').fetchone()[0],raw)


if __name__=='__main__':unittest.main()
