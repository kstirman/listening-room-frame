import hashlib,json,sqlite3,tempfile,unittest
from pathlib import Path
from PIL import Image
from photo_lab import Lab,evidence,choose_random,export,make_server
import socket,threading,http.client,re
from unittest.mock import patch

class LabTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);(self.root/'images').mkdir()
  photos={};features={}
  for i in range(12):
   k=f'{i+1:064x}';photos[k]=dict(content_key=k,media_key=k,date=f'{2010+i%6}-03-{i+1:02}',file='images/'+k+'.jpg',tags=[['activity','holding a dog']]);features[k]=['photograph',hashlib.sha256(k.encode()).hexdigest()[:16]]
   Image.new('RGB',(80,60),(i*20,80,120)).save(self.root/photos[k]['file'])
  self.data=dict(schema=1,created=1792423000,seed=1,recipes={'dogs':dict(id='dogs',title='Dogs held',family='care',explanation='Held dogs across years.',min_years=3,clauses=[dict(categories=['activity'],pattern='holding a dog')])},photos=photos,members={'dogs':list(photos)},features=features,exposures={k:[0,0] for k in photos},pairs=[])
  (self.root/'snapshot.json').write_text(json.dumps(self.data));self.lab=Lab(self.root)
 def tearDown(self):self.temp.cleanup()
 def test_reproducible_comparison_and_durable_reviews(self):
  a=self.lab.create_case('dogs',22);b=self.lab.create_case('dogs',22)
  self.assertEqual(a,b)
  for v in a['variants']:self.assertEqual([p['date'] for p in v['photos']],sorted(p['date'] for p in v['photos']))
  keys=[p['key'] for p in a['variants'][0]['photos']]
  self.lab.review(dict(case_id=a['id'],winner='neither',rejected=keys[:1],notes='Wrong match'))
  reload=Lab(self.root);saved=reload.status()['saved'][0];self.assertEqual(saved['review']['rejected'],keys[:1]);self.assertEqual(saved['case'],a)
 def test_no_source_snapshot_mutation_or_display_counts(self):
  before=(self.root/'snapshot.json').read_bytes();self.lab.create_case('dogs',1)
  self.assertEqual((self.root/'snapshot.json').read_bytes(),before)
  with sqlite3.connect(self.root/'planner/photo-themes.sqlite3') as db:
   self.assertEqual(db.execute('select count(*) from displays').fetchone()[0],0)
   self.assertEqual(db.execute('select count(*) from exposures').fetchone()[0],0)
 def test_missing_photos_cannot_be_selected(self):
  key=next(iter(self.data['photos']));(self.root/self.data['photos'][key]['file']).unlink()
  case=self.lab.create_case('dogs',3);self.assertFalse(any(p['key']==key for v in case['variants'] for p in v['photos']))
 def test_unknown_photo_review_rejected(self):
  case=self.lab.create_case('dogs',1)
  with self.assertRaises(ValueError):self.lab.review(dict(case_id=case['id'],winner='A',rejected=['bad']))
 def test_evidence_does_not_match_unrelated_category(self):
  r=self.data['recipes']['dogs'];self.assertEqual(evidence(r,[['color','holding a dog']]),[])
 def test_insufficient_diversity_is_explicit(self):
  for photo in self.lab.snapshot['photos'].values():photo['date']='2024-01-01'
  with self.assertRaises(ValueError):self.lab.create_case('dogs',1)
 def test_refuses_export_into_live_directory(self):
  with self.assertRaises(ValueError):export(dict(photo_stream_dir=str(self.root)),self.root)
 def test_export_is_bounded_private_and_read_only(self):
  source=self.root;keys=list(self.data['photos']);excluded=keys[0]
  with sqlite3.connect(source/'index.sqlite3') as db:
   db.execute('CREATE TABLE photos(key TEXT,content_key TEXT,url TEXT,date TEXT,active INTEGER,video INTEGER)')
   db.executemany('INSERT INTO photos VALUES(?,?,?,?,1,0)',[(k,k,'https://lh3.googleusercontent.com/private-secret',p['date']) for k,p in self.data['photos'].items()])
  with sqlite3.connect(source/'photo-themes.sqlite3') as db:
   db.executescript('CREATE TABLE recipes(id,payload);CREATE TABLE members(recipe,key);CREATE TABLE features(key,kind,dhash);CREATE TABLE exposures(key,count,last);CREATE TABLE pairs(a,b,count);')
   db.execute('INSERT INTO recipes VALUES(?,?)',('dogs',json.dumps(self.data['recipes']['dogs'])))
   db.executemany('INSERT INTO members VALUES(?,?)',[('dogs',k) for k in keys]);db.executemany('INSERT INTO features VALUES(?,?,?)',[(k,*v) for k,v in self.data['features'].items()])
  with sqlite3.connect(source/'analysis.sqlite3') as db:
   db.execute('CREATE TABLE photo_tags(content_key,category,value)');db.executemany('INSERT INTO photo_tags VALUES(?,?,?)',[(k,'activity','holding a dog') for k in keys])
  (source/'excluded.json').write_text(json.dumps(dict(schema=1,content_keys=[excluded],media_keys=[])))
  config=dict(photo_stream_dir=str(source),photo_analysis_file=str(source/'analysis.sqlite3'),photo_exclusions_file=str(source/'excluded.json'))
  paths=[source/n for n in ['index.sqlite3','photo-themes.sqlite3','analysis.sqlite3','excluded.json']];before={p:p.read_bytes() for p in paths}
  with tempfile.TemporaryDirectory() as other:
   result=export(config,Path(other),seed=4,recipe_count=1,per_recipe=8)
   raw=(Path(other)/'snapshot.json').read_text();snap=json.loads(raw)
   self.assertLessEqual(result['photos'],8);self.assertNotIn(excluded,snap['photos']);self.assertNotIn('private-secret',raw);self.assertNotIn('url',raw)
  self.assertTrue(all(p.read_bytes()==b for p,b in before.items()))
 def test_idle_connections_and_rendering_do_not_block_other_requests(self):
  server=make_server(self.root,0);port=server.server_port
  runner=threading.Thread(target=server.serve_forever,daemon=True);runner.start()
  idle=socket.create_connection(('127.0.0.1',port));started=threading.Event();release=threading.Event();result=[]
  def request(method,path,body=None,headers=None):
   conn=http.client.HTTPConnection('127.0.0.1',port,timeout=2)
   try:
    conn.request(method,path,body,headers or {});response=conn.getresponse();return response.status,response.read()
   finally:conn.close()
  def slow_case(*args):
   started.set();release.wait(5);return {'id':'synthetic'}
  try:
   status,html=request('GET','/');self.assertEqual(status,200)
   token=re.search(rb"const token='([^']+)'",html).group(1).decode()
   headers={'X-Lab-Token':token,'Content-Type':'application/json'};body=json.dumps({'recipe':'dogs','seed':1})
   with patch.object(Lab,'create_case',slow_case):
    worker=threading.Thread(target=lambda:result.append(request('POST','/api/case',body,headers)),daemon=True);worker.start()
    self.assertTrue(started.wait(2))
    self.assertEqual(request('GET','/api/state')[0],200)
    self.assertEqual(request('POST','/api/case',body,headers)[0],409)
    self.assertEqual(request('POST','/api/case',body)[0],403)
    release.set();worker.join(3);self.assertEqual(result[0][0],200)
  finally:
   release.set();idle.close();server.shutdown();server.server_close();runner.join(2)
 def test_unknown_recipe_rejected(self):
  with self.assertRaises(ValueError):self.lab.create_case('invented',1)

if __name__=='__main__':unittest.main()
