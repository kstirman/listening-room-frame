"""Private offline collage review. No TV writes or production display counters."""
import argparse
from contextlib import closing, contextmanager
from concurrent.futures import ThreadPoolExecutor
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
from pathlib import Path
import random
import re
import secrets
import sqlite3
import time
import threading
from urllib.parse import urlparse
from PIL import Image, ImageOps
import layouts
from photo_exclusions import Exclusions, valid_key
from photo_themes import Planner, similar


def read_db(path):
    return sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro', uri=True)


def save_json(path, data):
    temp=path.with_suffix('.tmp');temp.write_text(json.dumps(data,ensure_ascii=False));temp.chmod(0o600);temp.replace(path)


def evidence(recipe, tags):
    return [dict(category=c,value=v) for c,v in tags
            if any(c in clause['categories'] and re.search(clause['pattern'],v,re.I)
                   for clause in recipe['clauses'])]


def choose_random(rows, features, years, rng):
    for _ in range(80):
        shuffled=list(rows);rng.shuffle(shuffled);picked=[];days=set()
        for row in shuffled:
            if not row.get('date') or row['date'] in days:continue
            h=features.get(row['content_key'],['',''])[1]
            if any(similar(h,features.get(p['content_key'],['',''])[1]) for p in picked):continue
            picked.append(row);days.add(row['date'])
            if len(picked)==6:break
        if len(picked)==6 and len({p['date'][:4] for p in picked})>=years:return picked
    return None


def export(config, output, seed=1, recipe_count=3, per_recipe=12, extra_recipes=None):
    """Bounded private cohort; source databases opened read-only. No source metadata writes."""
    output=Path(output).resolve();source=Path(config['photo_stream_dir']).resolve()
    if output==source or source in output.parents:raise ValueError('Use a separate review directory')
    if (output/'snapshot.json').exists():raise ValueError('Use a fresh snapshot directory')
    output.mkdir(parents=True,exist_ok=True,mode=0o700);(output/'images').mkdir(exist_ok=True)
    exclusions=Exclusions(config)
    if not exclusions.valid:raise ValueError('Valid exclusions required')
    with closing(read_db(source/'index.sqlite3')) as db:
        db.row_factory=sqlite3.Row
        rows={r['content_key']:dict(r) for r in db.execute('SELECT * FROM photos WHERE active=1 AND video=0') if exclusions.allows(dict(r))}
    with closing(read_db(source/'photo-themes.sqlite3')) as db:
        recipes={k:json.loads(v) for k,v in db.execute('SELECT * FROM recipes')}
        members={k:[] for k in recipes}
        for recipe,key in db.execute('SELECT * FROM members'):
            if recipe in members and key in rows:members[recipe].append(key)
        features={k:[kind,h] for k,kind,h in db.execute('SELECT * FROM features')}
        exposure={k:[n,t] for k,n,t in db.execute('SELECT * FROM exposures')}
        pairs=[list(r) for r in db.execute('SELECT * FROM pairs')]
    with closing(read_db(config['photo_analysis_file'])) as db:
        tags={}
        for key,category,value in db.execute('SELECT content_key,category,value FROM photo_tags'):
            if key in rows:tags.setdefault(key,[]).append([category,value])
    for recipe in extra_recipes or []:
        ident=recipe['id'];recipes[ident]=recipe
        members[ident]=[k for k in rows if evidence(recipe,tags.get(k,[]))]
    rng=random.Random(seed);ids=sorted(recipes);rng.shuffle(ids)
    selected=[];cohorts={}
    for ident in ids:
        candidates=[rows[k] for k in members[ident] if features.get(k,[''])[0] in ('photograph','panorama')]
        trial=choose_random(candidates,features,recipes[ident].get('min_years',1),rng)
        if not trial:continue
        picked={r['content_key'] for r in trial};rng.shuffle(candidates)
        for r in candidates:
            if len(picked)>=per_recipe:break
            picked.add(r['content_key'])
        cohorts[ident]=sorted(picked);selected.append(ident)
        if len(selected)>=recipe_count:break
    photos={};total=0;failures=0
    from import_photos import get
    def preview(key):
        row=rows[key]
        if not Exclusions(config).allows(row):return None
        cached=source/'images'/(row['key']+'.jpg');resumed=output/'images'/(key+'.jpg')
        try:
            if resumed.is_file():blob=resumed.read_bytes()
            elif cached.is_file():blob=cached.read_bytes()
            else:blob=get(row['url']+'=w1600-h1200',12_000_000,{'lh3.googleusercontent.com'})
            with Image.open(io.BytesIO(blob)) as original:
                picture=ImageOps.exif_transpose(original).convert('RGB');picture.thumbnail((1600,1200),Image.Resampling.LANCZOS)
                stream=io.BytesIO();picture.save(stream,format='JPEG',quality=87)
            return stream.getvalue()
        except Exception:return None
    keys=sorted({k for group in cohorts.values() for k in group})
    # Bounded batches avoid retaining the whole image collection in memory.
    with ThreadPoolExecutor(max_workers=4) as pool:
        for start in range(0,len(keys),4):
            batch=keys[start:start+4]
            for key,blob in zip(batch,pool.map(preview,batch)):
                if blob is None:failures+=1;continue
                row=rows[key]
                if total+len(blob)>128*1024*1024 or not Exclusions(config).allows(row):continue
                target=output/'images'/(key+'.jpg');target.write_bytes(blob);target.chmod(0o600);total+=len(blob)
                photos[key]=dict(content_key=key,media_key=row['key'],date=row.get('date',''),file='images/'+key+'.jpg',tags=tags.get(key,[]))
    # Final exclusion check also removes identities blocked during acquisition.
    final=Exclusions(config)
    for key in list(photos):
        if not final.allows(dict(content_key=key,key=photos[key]['media_key'])):
            (output/photos.pop(key)['file']).unlink(missing_ok=True)
    selected=[k for k in selected if choose_random([photos[p] for p in cohorts[k] if p in photos],features,recipes[k].get('min_years',1),random.Random(seed))]
    editorial_order={r['id']:i for i,r in enumerate(extra_recipes or [])}
    selected.sort(key=lambda k:(k not in editorial_order,editorial_order.get(k,0),recipes[k]['title']))
    cohorts={k:cohorts[k] for k in selected}
    snapshot=dict(schema=1,created=time.time(),seed=seed,source_hashes={n:hashlib.sha256(Path(__file__).with_name(n).read_bytes()).hexdigest() for n in ['photo_themes.py','layouts.py','photo-recipes.json']},recipes={k:recipes[k] for k in selected},photos=photos,members={k:[p for p in v if p in photos] for k,v in cohorts.items()},features={k:features[k] for k in photos},exposures={k:exposure.get(k,[0,0]) for k in photos},pairs=[p for p in pairs if p[0] in photos and p[1] in photos],failures=failures)
    save_json(output/'snapshot.json',snapshot)
    return dict(photos=len(photos),recipes=len(selected),bytes=total,failures=failures)


class Lab:
    def __init__(self,root):
        self.root=Path(root).resolve();self.snapshot=json.loads((self.root/'snapshot.json').read_text())
        self.fingerprint=hashlib.sha256((self.root/'snapshot.json').read_bytes()).hexdigest()
        self.reviews=self.root/'reviews.sqlite3';self.renders=self.root/'renders';self.renders.mkdir(exist_ok=True)
        with self.connect() as db:
            db.executescript('CREATE TABLE IF NOT EXISTS cases(id TEXT PRIMARY KEY,payload TEXT); CREATE TABLE IF NOT EXISTS reviews(case_id TEXT PRIMARY KEY,payload TEXT,updated REAL);')
        self.reviews.chmod(0o600)
        # A disposable private index is enough for the production planner's active-member guard.
        scratch=self.root/'planner';scratch.mkdir(exist_ok=True)
        with sqlite3.connect(scratch/'index.sqlite3') as db:
            db.execute('CREATE TABLE IF NOT EXISTS photos(key TEXT PRIMARY KEY,active INTEGER,video INTEGER)')
            db.execute('DELETE FROM photos');db.executemany('INSERT INTO photos VALUES(?,1,0)',[(k,) for k in self.snapshot['photos']])
        self.planner=Planner(dict(photo_analysis_file=str(scratch/'unused.sqlite3'),photo_timezone='UTC'),scratch,clock=lambda:self.snapshot['created'])
    @contextmanager
    def connect(self):
        db=sqlite3.connect(self.reviews)
        try:
            with db:yield db
        finally:db.close()
    def eligible_rows(self,ident):
        photos=self.snapshot['photos']
        return [dict(photos[k],path=self.root/photos[k]['file']) for k in self.snapshot['members'][ident] if k in photos and (self.root/photos[k]['file']).is_file()]
    def production_pick(self,ident,seed):
        """Exact production assembly rule, with a disclosed forced-recipe/ready context."""
        s=self.snapshot;rows=self.eligible_rows(ident);recipe=s['recipes'][ident]
        p=self.planner;p.rng=random.Random(seed)
        p.history=lambda:rows
        p.data=lambda:({ident:recipe},{ident:set(s['members'][ident])},s['features'],s['exposures'],[('','','single')]*5,None)
        # No production writes: pair scoring uses only this disposable DB.
        with p.connect() as db:
            db.execute('DELETE FROM pairs');db.executemany('INSERT INTO pairs VALUES(?,?,?)',s['pairs'])
        # Paths remain inside the isolated snapshot; active key is the content hash.
        p.root=self.root
        # The index contains no URLs or live private configuration.
        with sqlite3.connect(self.root/'index.sqlite3') as db:
            db.execute('CREATE TABLE IF NOT EXISTS photos(key TEXT PRIMARY KEY,active INTEGER,video INTEGER)')
            db.execute('DELETE FROM photos');db.executemany('INSERT INTO photos VALUES(?,1,0)',[(r['content_key'],) for r in rows])
        show=p.presentation({})
        return show['photos'] if show and show['recipe']['id']==ident else None
    def create_case(self,ident,seed):
        if ident not in self.snapshot['recipes']:raise ValueError('Unknown recipe')
        a=self.production_pick(ident,seed);b=choose_random(self.eligible_rows(ident),self.snapshot['features'],self.snapshot['recipes'][ident].get('min_years',1),random.Random(seed))
        if not a or not b:raise ValueError('Not enough eligible dates/years in this snapshot')
        recipe=self.snapshot['recipes'][ident];variants=[('production assembly',sorted(a,key=lambda p:p.get('date',''))),('seeded eligible sample',sorted(b,key=lambda p:p.get('date','')))];random.Random(seed+41).shuffle(variants)
        ident_hash=hashlib.sha256((self.fingerprint+ident+str(seed)+Path(__file__).read_text()).encode()).hexdigest()[:24]
        case=dict(id=ident_hash,recipe=recipe,seed=seed,snapshot=self.fingerprint,created=self.snapshot['created'],variants=[])
        for label,(algorithm,photos) in zip(['A','B'],variants):
            target=self.renders/(ident_hash+'-'+label+'.jpg')
            # Hide semantic captions so the comparison is not primed by its title.
            layouts.photo_montage(photos,dict(title='',explanation=''),target,'gallery')
            case['variants'].append(dict(label=label,algorithm=algorithm,image='/render/'+target.name,photos=[dict(key=p['content_key'],date=p.get('date',''),evidence=evidence(recipe,p.get('tags',[])),views=self.snapshot['exposures'].get(p['content_key'],[0,0])[0]) for p in photos]))
        case['same_selection']=set(p['key'] for p in case['variants'][0]['photos'])==set(p['key'] for p in case['variants'][1]['photos'])
        with self.connect() as db:db.execute('INSERT OR REPLACE INTO cases VALUES(?,?)',(case['id'],json.dumps(case)))
        return case
    def review(self,data):
        if not isinstance(data,dict):raise ValueError('Review must be an object')
        with self.connect() as db:
            record=db.execute('SELECT payload FROM cases WHERE id=?',(data.get('case_id'),)).fetchone()
            if not record:raise ValueError('Unknown comparison')
            case=json.loads(record[0]);allowed={p['key'] for v in case['variants'] for p in v['photos']}
            if data.get('winner') not in ['A','B','both','neither','unsure']:raise ValueError('Invalid rating')
            rejected=data.get('rejected',[])
            if not isinstance(rejected,list) or len(rejected)>12 or any(k not in allowed for k in rejected):raise ValueError('Unknown photograph')
            notes=data.get('notes','')
            if not isinstance(notes,str) or len(notes)>4000:raise ValueError('Notes too long')
            clean=dict(case_id=case['id'],winner=data['winner'],rejected=sorted(set(rejected)),notes=notes)
            db.execute('INSERT OR REPLACE INTO reviews VALUES(?,?,?)',(case['id'],json.dumps(clean),time.time()))
        return clean
    def status(self):
        with self.connect() as db:
            saved=[dict(case=json.loads(c),review=json.loads(r) if r else None) for c,r in db.execute('SELECT c.payload,r.payload FROM cases c LEFT JOIN reviews r ON r.case_id=c.id ORDER BY c.rowid DESC LIMIT 100')]
        return dict(snapshot=self.fingerprint,created=self.snapshot['created'],photos=len(self.snapshot['photos']),recipes=[dict(id=k,title=v['title'],count=len(self.snapshot['members'][k]),explanation=v['explanation']) for k,v in self.snapshot['recipes'].items()],saved=saved)


def make_server(root,port=8767):
    lab=Lab(root);token=secrets.token_urlsafe(24)
    render_lock=threading.Lock()
    class Handler(BaseHTTPRequestHandler):
        timeout=15
        def log_message(self,*args):pass
        def allowed(self):
            host=self.headers.get('Host','');origin=self.headers.get('Origin')
            return host in {f'127.0.0.1:{port}',f'localhost:{port}'} and (not origin or origin in {f'http://127.0.0.1:{port}',f'http://localhost:{port}'})
        def reply(self,data,kind='application/json',status=200):
            if not isinstance(data,bytes):data=json.dumps(data).encode()
            self.send_response(status);self.send_header('Content-Type',kind);self.send_header('Content-Length',str(len(data)));self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff');self.end_headers();self.wfile.write(data)
        def do_GET(self):
            if not self.allowed():return self.reply({'error':'Local requests only'},status=403)
            path=urlparse(self.path).path
            if path=='/':
                html=Path(__file__).with_name('photo_lab.html').read_text().replace('__TOKEN__',token)
                return self.reply(html.encode(),'text/html; charset=utf-8')
            if path=='/api/state':return self.reply(lab.status())
            if path.startswith('/image/') and re.fullmatch(r'[a-f0-9]{64}\.jpg',path[7:]):
                key=path[7:-4]
                if key in lab.snapshot['photos']:
                    file=lab.root/'images'/path[7:]
                    if file.is_file():return self.reply(file.read_bytes(),'image/jpeg')
            if path.startswith('/render/') and re.fullmatch(r'[a-f0-9]{24}-[AB]\.jpg',path[8:]):
                file=lab.renders/path[8:]
                if file.is_file():return self.reply(file.read_bytes(),'image/jpeg')
            return self.reply({'error':'Not found'},status=404)
        def do_POST(self):
            if not self.allowed() or self.headers.get('X-Lab-Token')!=token:return self.reply({'error':'Local requests only'},status=403)
            try:
                length=int(self.headers.get('Content-Length','0'))
                if not 0<length<=16384:raise ValueError('Invalid request size')
                data=json.loads(self.rfile.read(length))
                if self.path=='/api/case':
                    seed=data.get('seed',1)
                    if not isinstance(seed,int) or not 0<=seed<2**31:raise ValueError('Invalid seed')
                    if not render_lock.acquire(blocking=False):
                        return self.reply({'error':'A comparison is already rendering. Try again shortly.'},status=409)
                    try:case=lab.create_case(data['recipe'],seed)
                    finally:render_lock.release()
                    return self.reply(case)
                if self.path=='/api/review':return self.reply(lab.review(data))
                return self.reply({'error':'Not found'},status=404)
            except (ValueError,KeyError,TypeError) as error:return self.reply({'error':str(error)},status=400)
    server=ThreadingHTTPServer(('127.0.0.1',port),Handler)
    port=server.server_port
    return server


def serve(root,port=8767):
    with make_server(root,port) as server:
        print(f'Photo review: http://127.0.0.1:{server.server_port}',flush=True)
        server.serve_forever()


def main():
    parser=argparse.ArgumentParser(description=__doc__);sub=parser.add_subparsers(dest='command',required=True)
    p=sub.add_parser('export');p.add_argument('--config',required=True);p.add_argument('--output',required=True);p.add_argument('--seed',type=int,default=1);p.add_argument('--recipes',type=int,choices=range(1,49),default=3);p.add_argument('--per-recipe',type=int,choices=range(6,25),default=12);p.add_argument('--connections')
    p=sub.add_parser('serve');p.add_argument('--root',required=True);p.add_argument('--port',type=int,default=8767)
    args=parser.parse_args()
    if args.command=='export':print(json.dumps(export(json.loads(Path(args.config).read_text()),args.output,args.seed,args.recipes,args.per_recipe,json.loads(Path(args.connections).read_text()) if args.connections else None)))
    else:serve(args.root,args.port)

if __name__=='__main__':main()
