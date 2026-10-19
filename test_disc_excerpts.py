import copy,unittest
from unittest.mock import Mock
import classical,timing,layouts

def rows():
 p=classical.Tracks()
 p.feed('''<div id="playerTracks"><p class="player__work">Example Symphony (A Composer)</p><div data-track="1"><span class="track__item--name">I. Allegro</span><p class="track__info">A Composer, Composer - A Conductor, Conductor - An Orchestra, Orchestra</p><span class="track__item--duration">10:00</span></div><p class="player__work" data-disk="2">DISC 2</p><div data-track="2"><span class="track__item--name">II. Andante</span><p class="track__info">A Composer, Composer - A Conductor, Conductor - An Orchestra, Orchestra</p><span class="track__item--duration">05:00</span></div><div data-track="3"><span class="track__item--name">Example Symphony: III. Finale</span><p class="track__info">A Composer, Composer - A Conductor, Conductor - An Orchestra, Orchestra - A Singer, Soloist</p><span class="track__item--duration">06:00</span></div></div>''')
 for row in p.tracks.values():row["genre"]="classique"
 return p.tracks

class DiscTests(unittest.TestCase):
 def test_cross_disc_with_matching_explicit_prefix_and_added_soloist(self):
  result=classical.catalog(rows())
  for i,k in enumerate(result):self.assertEqual(timing.composition(result,k),dict(index=i,durations=[600,300,360]))
 def test_no_borrowing_across_reset_conflict_or_credit_change(self):
  for field,value in [('title','I. Andante'),('title','Different Work: II. Andante'),('credits','Other Composer, Composer - Another, Conductor'),('continuation_heading','Different Symphony')]:
   r=rows();r['2'][field]=value
   result=classical.catalog(r)
   self.assertIsNone(timing.composition(result,'2'))
 def test_no_borrowing_after_unrelated_piece(self):
  r=rows();r['2']['title']='Independent Song'
  self.assertIsNone(timing.composition(classical.catalog(r),'3'))
 def test_missing_disc_evidence_does_not_infer_from_album(self):
  r=rows();r['2'].pop('catalog_disc')
  self.assertIsNone(timing.composition(classical.catalog(r),'2'))
 def test_lettered_sections_keep_source_heading(self):
  p=classical.Tracks();p.feed('<div id="playerTracks"><p class="player__work">A Passion</p>'+''.join(f'<div data-track="{i}"><span class="track__item--name">{title}</span></div>' for i,title in enumerate(['1. Chorus','27A. Recitative / B. Chorus','29. Chorale']))+'</div>')
  self.assertTrue(all(r.get('work')=='A Passion' for r in p.tracks.values()))
 def test_excerpt_context_requires_verified_source_and_increasing_sections(self):
  r={str(i):dict(work='A Passion',composer='Composer',work_scope=1,part_group=True,title=f'{n}. Section',movementName=f'{n}. Section',duration_seconds=100,_identity_album='a passion (excerpts)',_edition_verified='abc') for i,n in enumerate([1,8,18,27,29])}
  self.assertEqual(timing.composition(r,'2'),dict(index=2,durations=[100]*5,label='Excerpt'))
  for field,value in [('_identity_album','a passion'),('_edition_verified',''),('_edition_verified','other'),('movementName','1. Repeat')]:
   changed=copy.deepcopy(r);changed['3'][field]=value
   self.assertIsNone(timing.composition(changed,'2'))
 def test_excerpt_total_is_not_labeled_whole_work(self):
  draw=Mock();layouts.track_timing(draw,dict(duration_seconds=100,composition=dict(index=1,durations=[100]*24,label='Excerpt')),0,0,1000)
  texts=[c.args[1] for c in draw.text.call_args_list]
  self.assertTrue(any('Excerpt 2 of 24' in t for t in texts))
  self.assertTrue(any('Selected excerpts' in t for t in texts))
  self.assertFalse(any('Whole work' in t for t in texts))

class ExcerptSequenceTests(unittest.TestCase):
 def test_consecutive_excerpt_selection_is_still_labeled_excerpt(self):
  r={str(i):dict(work='A Work',composer='Composer',part_group=True,movementName=f'{i}. Section',duration_seconds=100,_identity_album='a work (excerpts)',_edition_verified='abc') for i in [1,2,3]}
  self.assertEqual(timing.composition(r,'2')['label'],'Excerpt')
