"""Offline photo recipes and confirmed-display history. Private SQLite, no network."""
from datetime import date,datetime
from contextlib import contextmanager
import hashlib,json,math,random,re,sqlite3,time
from pathlib import Path
from zoneinfo import ZoneInfo
from photo_exclusions import Exclusions,valid_key

SCHEMA='''
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT);
CREATE TABLE IF NOT EXISTS recipes(id TEXT PRIMARY KEY,payload TEXT);
CREATE TABLE IF NOT EXISTS members(recipe TEXT,key TEXT,PRIMARY KEY(recipe,key));
CREATE TABLE IF NOT EXISTS features(key TEXT PRIMARY KEY,kind TEXT,dhash TEXT);
CREATE TABLE IF NOT EXISTS exposures(key TEXT PRIMARY KEY,count INTEGER,last REAL);
CREATE TABLE IF NOT EXISTS history(key TEXT PRIMARY KEY,payload TEXT,last REAL);
CREATE TABLE IF NOT EXISTS displays(id TEXT PRIMARY KEY,at REAL,recipe TEXT,family TEXT,layout TEXT,members TEXT);
CREATE TABLE IF NOT EXISTS pairs(a TEXT,b TEXT,count INTEGER,PRIMARY KEY(a,b));
'''

def similar(a,b):
    try:return bool(a and b and len(a)==len(b)==16 and (int(a,16)^int(b,16)).bit_count()<=3)
    except (ValueError,TypeError):return False

class Planner:
    def __init__(self,config,root,clock=time.time,rng=None):
        self.config=config;self.root=Path(root);self.clock=clock;self.rng=rng or random
        self.path=self.root/'photo-themes.sqlite3'
        self.source=Path(config['photo_analysis_file'])
        self.recipe_file=Path(config.get('photo_recipes_file') or Path(__file__).with_name('photo-recipes.json'))
        with self.connect() as db:db.executescript(SCHEMA)
        self.path.chmod(0o600)
    @contextmanager
    def connect(self):
        db=sqlite3.connect(self.path,timeout=2)
        try:
            with db:yield db
        finally:db.close()
    def today(self):return datetime.fromtimestamp(self.clock(),ZoneInfo(self.config.get('photo_timezone','UTC'))).date()
    def refresh(self):
        """Run only in the prefetch worker; replace derived membership atomically."""
        stamp=str(self.source.stat().st_mtime_ns)+':'+hashlib.sha256(self.recipe_file.read_bytes()).hexdigest()
        with self.connect() as db:
            if db.execute('SELECT value FROM meta WHERE key="source"').fetchone()==(stamp,):return
        recipes=json.loads(self.recipe_file.read_text())
        assert isinstance(recipes,list) and len(recipes)<=200
        compiled=[(r,[(set(c['categories']),re.compile(c['pattern'],re.I)) for c in r['clauses']]) for r in recipes]
        with sqlite3.connect(self.source.as_uri()+'?mode=ro',uri=True) as src:
            tags={};features=[]
            for key,raw in src.execute('SELECT content_key,analysis_json FROM photo_analysis'):
                if not valid_key(key):continue
                obj=json.loads(raw);kind=obj.get('visual',{}).get('image',{}).get('kind','unknown')
                dhash=(obj.get('local_metrics') or {}).get('dhash','')
                features.append((key,kind,dhash))
                if kind in ('photograph','panorama'):tags[key]=[]
            for key,category,value in src.execute('SELECT content_key,category,value FROM photo_tags'):
                if key in tags:tags[key].append((category,value))
        members=[]
        for r,clauses in compiled:
            for key,values in tags.items():
                if all(any(c in cats and pattern.search(v) for c,v in values) for cats,pattern in clauses):members.append((r['id'],key))
        with self.connect() as db:
            db.execute('DELETE FROM members');db.execute('DELETE FROM recipes');db.execute('DELETE FROM features')
            db.executemany('INSERT INTO recipes VALUES(?,?)',[(r['id'],json.dumps(r)) for r in recipes])
            db.executemany('INSERT INTO members VALUES(?,?)',members);db.executemany('INSERT INTO features VALUES(?,?,?)',features)
            db.execute('INSERT OR REPLACE INTO meta VALUES("source",?)',(stamp,))
    def data(self):
        with self.connect() as db:
            recipes={k:json.loads(v) for k,v in db.execute('SELECT * FROM recipes')}
            members={k:set() for k in recipes}
            for recipe,key in db.execute('SELECT * FROM members'):
                if recipe in members:members[recipe].add(key)
            features={k:(kind,h) for k,kind,h in db.execute('SELECT * FROM features')}
            exposures={k:(n,t) for k,n,t in db.execute('SELECT * FROM exposures')}
            recent=list(db.execute('SELECT recipe,family,layout FROM displays ORDER BY at DESC LIMIT 100'))
            plan=db.execute('SELECT value FROM meta WHERE key="plan"').fetchone()
        return recipes,members,features,exposures,recent,json.loads(plan[0]) if plan else None
    def eligible(self,rows,features):
        exclude=Exclusions(self.config);seen=set();out=[]
        for r in rows:
            key=r.get('content_key')
            if not valid_key(key) or key in seen or not exclude.allows(dict(r,key=Path(r.get('file',r.get('key',''))).stem)):continue
            if key in features and features[key][0] not in ('photograph','panorama'):continue
            seen.add(key);out.append(r)
        return out
    def plan(self,rows,state):
        recipes,members,features,exposure,recent,plan=self.data()
        rows=self.eligible(rows,features);by={r['content_key']:r for r in rows}
        # Calendar membership uses the current source date, never a model guess.
        today=self.today();ann={k for k,r in by.items() if r.get('date','')[5:]==today.isoformat()[5:] and r['date']<today.isoformat()}
        month={k for k,r in by.items() if r.get('date','')[5:7]==today.isoformat()[5:7] and r['date']<today.isoformat()}
        for ident,keys,title,caption in [('on-this-day',ann,'On this day','The same calendar date, in different years.'),('this-month',month,today.strftime('%B')+' through the years','The same month, seen across different years.')]:
            recipes[ident]=dict(id=ident,title=title,family='calendar',explanation=caption);members[ident]=keys
        recent_keys=set(state.get('photo_recent',[]))
        fresh={k for k,r in by.items() if 'images/'+r['key']+'.jpg' not in recent_keys}
        candidates={}
        for ident,r in recipes.items():
            keys=members[ident]&fresh;days={by[k].get('date') for k in keys}-{None,''}
            if len(keys)>=8 and len(days)>=6:candidates[ident]=keys
        # Keep a theme until seven of its newly prepared photos really reached TV.
        if plan and plan.get('remaining',0)>0 and plan['id'] in candidates:chosen=plan['id']
        elif candidates:
            last_families=[f for _,f,l in recent if l!='single'][:2]
            last_recipes=[r for r,_,l in recent if l!='single'][:12]
            choices=[k for k in candidates if recipes[k]['family'] not in last_families and k not in last_recipes] or [k for k in candidates if k not in last_recipes] or list(candidates)
            counts={k:sum(r==k for r,_,_ in recent) for k in choices}
            # Calendar stories occasionally get priority when they have material.
            calendar=[k for k in choices if recipes[k]['family']=='calendar']
            chosen=self.rng.choice(calendar) if calendar and self.rng.random()<.25 else self.rng.choices(choices,weights=[1/(1+counts[k]) for k in choices])[0]
            plan=dict(id=chosen,remaining=7)
            with self.connect() as db:db.execute('INSERT OR REPLACE INTO meta VALUES("plan",?)',(json.dumps(plan),))
        else:chosen=None
        self.selection=(by,candidates.get(chosen,set()),features,exposure,recipes.get(chosen))
        return rows
    def choose(self,rows,planned,excluded):
        by,pool,features,exposure,recipe=self.selection
        fresh=[i for i,r in enumerate(rows) if i not in excluded and r.get('content_key') in by and 'images/'+r['key']+'.jpg' not in planned.get('photo_recent',[])]
        candidates=[i for i in fresh if rows[i]['content_key'] in pool] or fresh
        if not candidates:return None
        used=[r for i,r in enumerate(rows) if i in excluded]
        used_days={r.get('date') for r in used}|set(planned.get('photo_recent_dates',[])[-7:])
        distinct=[i for i in candidates if rows[i].get('date') not in used_days]
        candidates=distinct or candidates
        hashes=[features.get(r.get('content_key'),('',''))[1] for r in used]
        candidates=[i for i in candidates if not any(similar(features.get(rows[i]['content_key'],('',''))[1],h) for h in hashes)]
        if not candidates:return None
        cutoff=self.today().replace(year=self.today().year-5) if not (self.today().month==2 and self.today().day==29) else date(self.today().year-5,2,28)
        recent=[i for i in candidates if rows[i].get('date','')>=cutoff.isoformat()];old=[i for i in candidates if i not in recent]
        candidates=(recent if self.rng.random()<.6 else old) or candidates
        # Balance capture dates first, so bursts do not dominate a recipe.
        days={}
        for i in candidates:days.setdefault(rows[i].get('date',''),[]).append(i)
        day=self.rng.choices(list(days),weights=[1/(1+min(exposure.get(rows[i]['content_key'],(0,0))[0] for i in group)) for group in days.values()])[0]
        group=days[day];i=self.rng.choices(group,weights=[1/(1+exposure.get(rows[j]['content_key'],(0,0))[0]) for j in group])[0]
        return i
    def annotate(self,row):
        recipe=self.selection[4]
        if recipe and row.get('content_key') in self.selection[1]:row=dict(row,theme=recipe)
        return row
    def history(self):
        with self.connect() as db:rows=[json.loads(r[0]) for r in db.execute('SELECT payload FROM history ORDER BY last DESC LIMIT 28')]
        return rows
    def presentation(self,state):
        """Only already displayed cached images; no downloads or model work."""
        recipes,members,features,exposures,recent,_=self.data()
        ordinary_ready=sum(1 for _,_,layout in recent[:5] if layout=='single')>=5
        rows=self.eligible(self.history(),features)
        # Index removal is authoritative, independently of historical analysis.
        with sqlite3.connect((self.root/'index.sqlite3').as_uri()+'?mode=ro',uri=True) as db:
            active={r[0] for r in db.execute('SELECT key FROM photos WHERE active=1 AND video=0')}
        rows=[dict(r,path=self.root/r['file'],key=r['file']) for r in rows if Path(r['file']).stem in active and (self.root/r['file']).is_file()]
        distinct=[]
        for row in rows:
            h=features.get(row['content_key'],('',''))[1]
            if not any(similar(h,features.get(r['content_key'],('',''))[1]) for r in distinct):distinct.append(row)
        rows=distinct
        if len(rows)<6:return None
        last_themes=[r for r,_,l in recent if l!='single'][:8]
        options=[]
        for r in rows:
            theme=r.get('theme')
            if theme and theme.get('family')!='calendar':recipes.setdefault(theme['id'],theme);members.setdefault(theme['id'],{p['content_key'] for p in rows if p.get('theme',{}).get('id')==theme['id']})
        today=self.today()
        for ident,segment,title,caption in [('on-this-day',slice(5,None),'On this day','The same calendar date, in different years.'),('this-month',slice(5,7),today.strftime('%B')+' through the years','The same month, seen across different years.')]:
            recipes[ident]=dict(id=ident,title=title,family='calendar',explanation=caption)
            members[ident]={r['content_key'] for r in rows if r.get('date','')[segment]==today.isoformat()[segment] and r['date']<today.isoformat()}
        for ident,recipe in recipes.items():
            if ident in last_themes:continue
            selected=[];days=set()
            for r in sorted(rows,key=lambda r:exposures.get(r['content_key'],(0,0))[0]):
                if r['content_key'] not in members.get(ident,set()) or r.get('date') in days:continue
                if any(similar(features.get(r['content_key'],('',''))[1],features.get(x['content_key'],('',''))[1]) for x in selected):continue
                selected.append(r);days.add(r.get('date'))
                if len(selected)==6:break
            if len(selected)==6 and (('years' not in (recipe['title']+' '+recipe['explanation']) and recipe['family']!='calendar') or len({p.get('date','')[:4] for p in selected})>=2):options.append((recipe,selected))
        # A 28-photo retrospective reuses history; it need not invent one theme.
        if len(rows)==28 and not any(l=='mosaic' for _,_,l in recent[:16]):
            return dict(recipe=dict(id='recent-28',family='retrospective',title='The last 28, together',explanation='A look back at the photographs recently on the frame.'),photos=list(reversed(rows)),layout='mosaic')
        if not ordinary_ready or not options:return None
        with self.connect() as db:
            pairs={(a,b):n for a,b,n in db.execute('SELECT * FROM pairs')}
        def repetition(option):
            keys=[p['content_key'] for p in option[1]]
            return sum(pairs.get(tuple(sorted((a,b))),0) for i,a in enumerate(keys) for b in keys[i+1:])
        recipe,selected=self.rng.choices(options,weights=[1/(1+repetition(o)) for o in options])[0]
        layout='timeline' if recipe['family']=='calendar' else ('gallery' if sum(l=='gallery' for _,_,l in recent[:2])==0 else 'pairs')
        return dict(recipe=recipe,photos=sorted(selected,key=lambda r:r.get('date','')),layout=layout)
    def record(self,receipt,photos,presentation=None):
        """Call only after confirmed TV selection. Duplicate receipts are harmless."""
        with self.connect() as db:
            if db.execute('SELECT 1 FROM displays WHERE id=?',(receipt,)).fetchone():return
        now=self.clock();photos=self.eligible(photos,self.data()[2])
        if not photos:return
        recipe=(presentation or {}).get('recipe',{});layout=(presentation or {}).get('layout','single')
        if not recipe:recipe=photos[0].get('theme',{})
        keys=[r['content_key'] for r in photos]
        with self.connect() as db:
            if db.execute('SELECT 1 FROM displays WHERE id=?',(receipt,)).fetchone():return
            db.execute('INSERT INTO displays VALUES(?,?,?,?,?,?)',(receipt,now,recipe.get('id',''),recipe.get('family',''),layout,json.dumps(keys)))
            for row in photos:
                key=row['content_key'];saved={k:v for k,v in row.items() if k not in ('path','key')};saved.setdefault('file',row.get('key'))
                db.execute('INSERT INTO exposures VALUES(?,1,?) ON CONFLICT(key) DO UPDATE SET count=count+1,last=excluded.last',(key,now))
                if layout=='single':db.execute('INSERT OR REPLACE INTO history VALUES(?,?,?)',(key,json.dumps(saved),now))
            for a in range(len(keys)):
                for b in range(a+1,len(keys)):
                    x,y=sorted([keys[a],keys[b]]);db.execute('INSERT INTO pairs VALUES(?,?,1) ON CONFLICT(a,b) DO UPDATE SET count=count+1',(x,y))
            db.execute('DELETE FROM history WHERE key NOT IN (SELECT key FROM history ORDER BY last DESC LIMIT 28)')
            if layout=='single':
                plan=db.execute('SELECT value FROM meta WHERE key="plan"').fetchone()
                if plan:
                    plan=json.loads(plan[0]);plan['remaining']-=sum(r.get('theme',{}).get('id')==plan['id'] for r in photos)
                    db.execute('UPDATE meta SET value=? WHERE key="plan"',(json.dumps(plan),))
