import hashlib,json,random,sqlite3,tempfile,unittest
from pathlib import Path
from unittest.mock import Mock,patch
from PIL import Image
from photo_themes import Planner,similar
from photo_stream import Index,Stream
from bridge import Bridge
import layouts

class ThemeTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);(self.root/'images').mkdir()
  self.source=self.root/'analysis.sqlite3'
  with sqlite3.connect(self.source) as db:
   db.executescript('CREATE TABLE photo_analysis(content_key TEXT,analysis_json TEXT);CREATE TABLE photo_tags(content_key TEXT,category TEXT,value TEXT);')
   for i in range(40):
    k=f'{i+1:064x}';data=dict(visual={'image':{'kind':'photograph'}},local_metrics={'dhash':hashlib.sha256(k.encode()).hexdigest()[:16]})
    db.execute('INSERT INTO photo_analysis VALUES(?,?)',(k,json.dumps(data)))
    db.execute('INSERT INTO photo_tags VALUES(?,?,?)',(k,'color','red' if i<20 else 'blue'))
  self.recipefile=self.root/'recipes.json';self.recipefile.write_text(json.dumps([dict(id=c,title=c,family=c,explanation='Shared color.',clauses=[dict(categories=['color'],pattern=c)]) for c in ['red','blue']]))
  self.config=dict(photo_analysis_file=str(self.source),photo_recipes_file=str(self.recipefile),photo_timezone='America/Los_Angeles')
  self.p=Planner(self.config,self.root,clock=lambda:1792368000,rng=random.Random(3));self.p.refresh()
  self.index=Index(self.root/'index.sqlite3')
  self.rows=[dict(key=f'{i+1:064x}',content_key=f'{i+1:064x}',date=f'{2020+i%6}-03-{i%28+1:02}',width=200,height=100,video=False,url='https://lh3.googleusercontent.com/synthetic') for i in range(40)]
  self.index.upsert(self.rows,'a')
 def tearDown(self):self.index.close();self.temp.cleanup()
 def photo(self,i,theme='red'):
  r=self.rows[i];file='images/'+r['key']+'.jpg';Image.new('RGB',(200,100),'navy').save(self.root/file)
  return dict(file=file,key=file,path=self.root/file,content_key=r['content_key'],date=r['date'],caption=r['date'],theme=dict(id=theme,title=theme,family=theme,explanation='Shared color.'))
 def test_refresh_updates_new_analysis_without_replacing_history(self):
  self.p.record('x',[self.photo(0)])
  with sqlite3.connect(self.source) as db:db.execute('INSERT INTO photo_tags VALUES(?,?,?)',(self.rows[30]['content_key'],'color','red'))
  self.p.refresh();self.assertIn(self.rows[30]['content_key'],self.p.data()[1]['red']);self.assertEqual(self.p.data()[3][self.rows[0]['content_key']][0],1)
 def test_plan_preparation_does_not_count_as_display(self):
  rows=self.p.plan(self.rows,{});i=self.p.choose(rows,{},set());self.assertIsNotNone(i);self.assertEqual(self.p.data()[3],{})
 def test_receipt_idempotence_and_plan_advancement(self):
  self.p.plan(self.rows,{})
  ident=self.p.data()[-1]['id'];i=0 if ident=='red' else 20
  self.p.record('x',[self.photo(i,ident)]);self.p.record('x',[self.photo(i,ident)])
  self.assertEqual(self.p.data()[3][self.rows[i]['content_key']][0],1);self.assertEqual(self.p.data()[-1]['remaining'],6)
 def test_history_bounded_to_28_and_mosaic_uses_only_history(self):
  for i in range(35):self.p.record(str(i),[self.photo(i,'red' if i<20 else 'blue')])
  self.assertEqual(len(self.p.history()),28)
  show=self.p.presentation({});self.assertEqual(show['layout'],'mosaic');self.assertEqual(len(show['photos']),28)
  self.assertTrue(all(r['path'].exists() for r in show['photos']))
 def test_first_28_does_not_wait_behind_smaller_montage(self):
  for i in range(27):self.p.record(str(i),[self.photo(i)])
  self.p.record('small',[self.photo(i) for i in range(6)],dict(recipe=dict(id='red',family='red'),layout='gallery'))
  self.p.record('last',[self.photo(27)])
  self.assertEqual(self.p.presentation({})['layout'],'mosaic')
 def test_montage_receipt_does_not_reorder_single_history(self):
  for i in range(8):self.p.record(str(i),[self.photo(i)])
  before=[r['content_key'] for r in self.p.history()];show=self.p.presentation({});self.assertIsNotNone(show)
  self.p.record('montage',show['photos'],show);self.assertEqual(before,[r['content_key'] for r in self.p.history()]);self.assertIsNone(self.p.presentation({}))
 def test_carried_dog_recipe_does_not_match_carried_child_near_dog(self):
  import re
  recipe=next(r for r in json.loads(Path(layouts.__file__).with_name('photo-recipes.json').read_text()) if r['id']=='story-dogsheld')
  pattern=re.compile(recipe['clauses'][0]['pattern'],re.I)
  self.assertIsNone(pattern.search('holding a child while a dog stands nearby'))
  self.assertIsNone(pattern.search('holding dog leashes'))
  self.assertTrue(pattern.search('holding a small dog'))
  self.assertTrue(pattern.search('dog on her lap'))
 def test_retired_recipe_cannot_return_from_old_photo_annotations(self):
  for i in range(8):self.p.record(str(i),[self.photo(i)])
  self.recipefile.write_text('[]');self.p.refresh()
  self.assertIsNone(self.p.presentation({}))
 def test_story_requires_distinct_years_and_prefers_year_diversity(self):
  recipes=json.loads(self.recipefile.read_text());recipes[0]['min_years']=3
  self.recipefile.write_text(json.dumps(recipes));self.p.refresh()
  for i in range(8):
   photo=self.photo(i);photo['date']=f'2024-03-{i+1:02}';self.p.record(str(i),[photo])
  self.assertIsNone(self.p.presentation({}))
  for i,year in [(6,2010),(7,2018)]:
   photo=self.photo(i);photo['date']=f'{year}-03-{i+1:02}';self.p.record('new'+str(i),[photo])
  show=self.p.presentation({});self.assertIsNotNone(show)
  self.assertGreaterEqual(len({r['date'][:4] for r in show['photos']}),3)
 def test_exclusion_change_removes_historical_members(self):
  for i in range(6):self.p.record(str(i),[self.photo(i)])
  self.assertIsNotNone(self.p.presentation({}))
  excluded=self.root/'excluded.json';excluded.write_text(json.dumps(dict(schema=1,content_keys=[self.rows[0]['content_key']],media_keys=[])))
  self.p.config['photo_exclusions_file']=str(excluded);self.assertIsNone(self.p.presentation({}))
  excluded.write_text('broken');self.assertIsNone(self.p.presentation({}))
 def test_removed_album_membership_cannot_reappear_from_history(self):
  for i in range(6):self.p.record(str(i),[self.photo(i)])
  self.index.db.execute('UPDATE photos SET active=0 WHERE key=?',(self.rows[0]['key'],));self.index.db.commit()
  self.assertIsNone(self.p.presentation({}))
 def test_near_duplicates_not_used_together(self):
  self.assertTrue(similar('0123456789abcdef','0123456789abcdee'))
  for i in range(6):self.p.record(str(i),[self.photo(i)])
  with self.p.connect() as db:db.execute('UPDATE features SET dhash="0123456789abcdef"')
  self.assertIsNone(self.p.presentation({}))
 def test_unknown_and_nonphoto_analysis(self):
  with self.p.connect() as db:db.execute('UPDATE features SET kind="screenshot" WHERE key=?',(self.rows[0]['content_key'],))
  rows=self.p.plan(self.rows,{})
  self.assertNotIn(self.rows[0],rows)
 def test_recent_photos_and_dates_avoided(self):
  rows=self.p.plan(self.rows,{})
  i=self.p.choose(rows,{},set());r=rows[i]
  j=self.p.choose(rows,dict(photo_recent=['images/'+r['key']+'.jpg'],photo_recent_dates=[r['date']]),{i})
  self.assertNotEqual(i,j);self.assertNotEqual(r['date'],rows[j]['date'])
 def test_calendar_uses_source_date_and_local_day(self):
  self.assertEqual(self.p.today().isoformat(),'2026-10-18')
 def test_calendar_history_cannot_claim_yesterday_is_today(self):
  with self.p.connect() as db:db.execute('DELETE FROM members')
  for i in range(6):
   photo=self.photo(i);photo['date']=f'{2010+i}-10-18';photo['theme']=dict(id='on-this-day',title='On this day',family='calendar',explanation='The same calendar date, in different years.')
   self.p.record(str(i),[photo])
  show=self.p.presentation({});self.assertIsNotNone(show)
  self.assertEqual(show['recipe']['family'],'calendar')
  self.p.clock=lambda:1792368000+32*86400
  self.assertIsNone(self.p.presentation({}))
 def test_no_history_means_no_montage(self):self.assertIsNone(self.p.presentation({}))
 def test_layout_variants_are_4k(self):
  photos=[self.photo(i) for i in range(28)]
  for kind,count in [('gallery',6),('pairs',6),('timeline',6),('mosaic',28)]:
   target=self.root/(kind+'.jpg');layouts.photo_montage(photos[:count],dict(title='Synthetic connection',explanation='A factual explanation.'),target,kind)
   with Image.open(target) as image:self.assertEqual(image.size,(3840,2160))
 def test_mosaic_tiles_fill_frame_with_only_narrow_seams(self):
  for ratios in [[.65,1.5,1,2]*7,[.66]*28,[1.5]*28]:
   boxes=layouts.mosaic_boxes(ratios);self.assertEqual(len(boxes),28)
   self.assertEqual(min(b[0] for b in boxes),0);self.assertEqual(min(b[1] for b in boxes),0)
   self.assertEqual(max(b[2] for b in boxes),3840);self.assertEqual(max(b[3] for b in boxes),2160)
   self.assertGreater(sum((r-x)*(b-y) for x,y,r,b in boxes),3840*2160*.97)
   for i,(x,y,r,b) in enumerate(boxes):
    self.assertTrue(0<=x<r<=3840 and 0<=y<b<=2160)
    for xx,yy,rr,bb in boxes[:i]:self.assertTrue(r<=xx or rr<=x or b<=yy or bb<=y)
 def test_mosaic_image_reaches_all_four_corners(self):
  target=self.root/'full-bleed.png'
  layouts.photo_montage([self.photo(i) for i in range(28)],dict(title='',explanation=''),target,'mosaic')
  with Image.open(target) as image:
   for point in [(0,0),(3839,0),(0,2159),(3839,2159)]:
    r,g,b=image.getpixel(point);self.assertGreater(b,r+15);self.assertGreater(b,g+15)
 def test_six_landscapes_use_screen_width_in_pair_layout(self):
  target=self.root/'pairs.jpg'
  layouts.photo_montage([self.photo(i) for i in range(6)],dict(title='Shared color',explanation='Synthetic photos.'),target,'pairs')
  with Image.open(target) as image:
   thumb=image.resize((320,180));blue=sum(b>60 and r<40 and g<40 for r,g,b in thumb.getdata())
  self.assertGreater(blue,320*180*.52)
 def test_production_confirmed_selection_boundary(self):
  # The receipt must never run when TV selection fails.
  b=Bridge(dict(idle_photos_enabled=True,photo_themes_enabled=True),self.root/'bridge')
  b.photo_stream=Mock();b.photo_stream.themes=self.p;b.photo_stream.available.return_value=[self.photo(0)]
  tv=Mock();tv.get_artmode.return_value='on';tv.get_current.return_value={'content_id':'original'};tv.upload.return_value='upload'
  b.television=Mock(return_value=tv);b.select=Mock(side_effect=RuntimeError('not confirmed'))
  with self.assertRaises(RuntimeError):b.show_idle_photo()
  self.assertEqual(self.p.data()[3],{})

class ThemeIntegrationTests(unittest.TestCase):
 setUp=ThemeTests.setUp
 tearDown=ThemeTests.tearDown
 photo=ThemeTests.photo
 def test_restart_retains_established_photo_idle_state(self):
  root=self.root/'bridge';root.mkdir();(root/'state.json').write_text(json.dumps(dict(view='photo')))
  b=Bridge(dict(idle_photos_enabled=True,idle_seconds=120),root)
  b.playback=Mock(return_value=('PAUSED_PLAYBACK',{}));b.show_idle_photo=Mock()
  b.step();b.show_idle_photo.assert_called_once()
 def test_confirmed_receipt_and_music_resume(self):
  b=Bridge(dict(idle_photos_enabled=True,photo_themes_enabled=True,idle_seconds=0),self.root/'bridge')
  b.photo_stream=Mock();b.photo_stream.themes=self.p;b.photo_stream.available.return_value=[self.photo(0)]
  current=['original'];tv=Mock();tv.get_artmode.return_value='on';tv.get_current.side_effect=lambda:{'content_id':current[0]};tv.select_image.side_effect=lambda c:current.__setitem__(0,c);tv.upload.side_effect=['photo','music']
  b.television=Mock(return_value=tv);b.show_idle_photo()
  self.assertEqual(self.p.data()[3][self.rows[0]['content_key']][0],1)
  self.assertEqual(b.state['photo_receipts'][0]['id'],'photo')
  b.playback=Mock(return_value=('PLAYING',dict(album_id='synthetic')));b.details=Mock(return_value=None);b.card=Mock(return_value=(self.photo(0)['path'],'Synthetic'));b.next_collage=10**20
  b.step();self.assertEqual(current[0],'music');self.assertNotEqual(b.state['view'],'photo')
 def test_theme_prefetch_is_bounded_and_keeps_displayed_history(self):
  import io
  out=io.BytesIO();Image.new('RGB',(200,100),'red').save(out,format='JPEG')
  fetch=Mock(return_value=out.getvalue())
  config=dict(self.config,photo_themes_enabled=True,photo_stream_dir=str(self.root),photo_stream_url_file='unused',photo_prefetch_count=3)
  stream=Stream(config,client=Mock(),fetch=fetch,clock=lambda:1792368000)
  stream.fill(self.index);self.assertEqual(len(stream.queued()),3);self.assertEqual(fetch.call_count,3)
  self.assertTrue(all(p.get('theme') for p in stream.queued()))
  self.assertEqual(stream.themes.data()[3],{})
  row=stream.available()[0];stream.themes.record('accepted',[row]);stream.state=dict(photo_key=row['key'],photo_recent=[row['key']])
  stream.fill(self.index);self.assertTrue(row['path'].exists());self.assertLessEqual(len(list(stream.images.glob('*.jpg'))),8)
 def test_analysis_failure_falls_back_without_blocking_photos(self):
  import io
  out=io.BytesIO();Image.new('RGB',(200,100),'red').save(out,format='JPEG')
  config=dict(self.config,photo_themes_enabled=True,photo_stream_dir=str(self.root),photo_stream_url_file='unused',photo_prefetch_count=3,photo_analysis_file=str(self.root/'missing.sqlite3'))
  stream=Stream(config,client=Mock(),fetch=Mock(return_value=out.getvalue()),clock=lambda:1792368000)
  stream.fill(self.index);self.assertEqual(len(stream.available()),3)
