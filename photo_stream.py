"""Private metadata index and bounded background photo queue; no TV operations."""
import copy
import fcntl
import io
import json
import logging
import os
from pathlib import Path
import re
import sqlite3
import threading
import time
from PIL import Image, ImageOps
import idle_photos
import photo_metadata
from photo_themes import Planner
from photo_places import Places
from shared_album import Client, AlbumError
from import_photos import get
from photo_exclusions import Exclusions

LOG=logging.getLogger(__name__)


def atomic_json(path,value):
    temp=path.with_suffix('.tmp')
    temp.write_text(json.dumps(value))
    temp.chmod(0o600)
    temp.replace(path)


class Index:
    def __init__(self,path):
        self.path=Path(path)
        self.db=sqlite3.connect(path,timeout=10)
        self.db.row_factory=sqlite3.Row
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS photos (
              key TEXT PRIMARY KEY, url TEXT NOT NULL, date TEXT, width INTEGER,
              height INTEGER, video INTEGER, active INTEGER DEFAULT 1, generation TEXT,
              extra TEXT DEFAULT '{}', failure_until REAL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY,value TEXT);
        ''')
        if 'content_key' not in {r[1] for r in self.db.execute('PRAGMA table_info(photos)')}:
            self.db.execute('ALTER TABLE photos ADD COLUMN content_key TEXT')
            self.db.commit()
        self.path.chmod(0o600)

    def close(self):self.db.close()

    def setting(self,key,default=0):
        row=self.db.execute('SELECT value FROM settings WHERE key=?',(key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set(self,key,value):
        self.db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)',(key,json.dumps(value)))
        self.db.commit()

    def upsert(self,rows,generation):
        self.db.executemany('''INSERT INTO photos(key,url,date,width,height,video,generation,content_key)
          VALUES(:key,:url,:date,:width,:height,:video,:generation,:content_key)
          ON CONFLICT(key) DO UPDATE SET url=excluded.url,date=excluded.date,
          width=excluded.width,height=excluded.height,video=excluded.video,
          generation=excluded.generation,active=1,
          content_key=COALESCE(excluded.content_key,photos.content_key),
          failure_until=CASE WHEN photos.url!=excluded.url THEN 0 ELSE photos.failure_until END''',[dict(r,generation=generation,content_key=r.get('content_key')) for r in rows])
        self.db.commit()

    def finish(self,generation,now):
        self.db.execute('UPDATE photos SET active=0 WHERE generation!=?',(generation,))
        self.db.commit();self.set('last_full',now)

    def rows(self,now=0):
        return [dict(r) for r in self.db.execute('SELECT * FROM photos WHERE active=1 AND video=0 AND failure_until<=?',(now,))]

    def extra(self,key,fields):
        self.db.execute('UPDATE photos SET extra=? WHERE key=?',(json.dumps(fields),key));self.db.commit()

    def failed(self,key,until):
        self.db.execute('UPDATE photos SET failure_until=? WHERE key=?',(until,key));self.db.commit()

    def import_metadata(self,manifest):
        for row in manifest.get('photos',[]):
            match=re.fullmatch(r'images/([a-f0-9]{64})\.jpg',row.get('file',''))
            if match:
                existing=self.db.execute('SELECT extra FROM photos WHERE key=?',(match[1],)).fetchone()
                if existing and existing[0]=='{}':
                    fields={k:v for k,v in row.items() if k not in ('file','date','date_source')}
                    self.extra(match[1],fields)


class Stream:
    def __init__(self,config,client=None,fetch=None,clock=None):
        self.config=config
        self.root=Path(config['photo_stream_dir']).expanduser().resolve()
        self.root.mkdir(parents=True,exist_ok=True,mode=0o700)
        self.images=self.root/'images';self.images.mkdir(exist_ok=True,mode=0o700)
        self.manifest=self.root/'ready.json';self.status=self.root/'status.json'
        self.client=client or Client(config['photo_stream_url_file'])
        self.fetch=fetch or get;self.clock=clock or time.time
        self.places=Places(config.get('photo_places_file'))
        self.count=max(2,min(20,int(config.get('photo_prefetch_count',10))))
        self.budget=max(32,min(1024,int(config.get('photo_cache_mb',128))))*1024*1024
        self.themes=None
        if config.get('photo_themes_enabled') and config.get('photo_analysis_file'):
            try:self.themes=Planner(config,self.root,self.clock)
            except (OSError,sqlite3.Error,ValueError):LOG.warning('Photo themes unavailable; ordinary rotation retained')
        self.thread=None;self.lock=threading.Lock();self.state={};self.next_tick=0
        self.startup=True
        try:
            import pillow_heif
            pillow_heif.register_heif_opener()
        except ImportError:
            pass

    def tick(self,state):
        # Only a tiny state snapshot crosses into the worker. No network/DB on UI path.
        with self.lock:
            self.state={k:copy.deepcopy(v) for k,v in state.items() if k.startswith('photo_')}
            if self.thread and self.thread.is_alive():return
            if self.clock()<self.next_tick:return
            self.next_tick=self.clock()+15
            self.thread=threading.Thread(target=self.work,daemon=True,name='photo-prefetch')
            self.thread.start()

    def snapshot(self):
        with self.lock:return copy.deepcopy(self.state)

    def available(self):
        return idle_photos.load(dict(self.config,idle_photos_enabled=True,idle_photos_file=str(self.manifest)))

    def refresh(self,index,full):
        now=self.clock();generation=str(now)
        maximum=250 if full else 3
        complete=False;pages=0;count=0
        for rows,complete in self.client.pages(maximum):
            pages+=1;count+=len(rows)
            if count>75000:raise AlbumError('Album index limit exceeded')
            index.upsert(rows,generation)
        if full and not complete:raise AlbumError('Full metadata scan incomplete')
        # Only a successfully terminated full scan may remove records.
        if complete:
            if count==0 and index.rows():raise AlbumError('Empty refresh retained prior index')
            index.finish(generation,now)
        index.set('last_recent',now)
        return pages

    def prepare(self,row,index):
        if not Exclusions(self.config).allows(row):
            raise ValueError('Photo excluded')
        key=row['key'];target=self.images/(key+'.jpg')
        extra=json.loads(row['extra'])
        original=(not extra.get('original_metadata_checked') and
                  self.clock()-extra.get('original_metadata_attempted',0)>=7*86400)
        blob=None
        if original:
            try:
                blob=self.fetch(row['url']+'=d',30_000_000,{'lh3.googleusercontent.com'})
                with Image.open(io.BytesIO(blob)) as image:
                    extra.update(photo_metadata.extract(image));extra['original_metadata_checked']=True
                    # Verify decodability before choosing the original as display input.
                    image.load()
            except Exception:
                blob=None
            extra['original_metadata_attempted']=self.clock()
        if not target.exists():
            if blob is None:blob=self.fetch(row['url']+'=w3840-h2160',30_000_000,{'lh3.googleusercontent.com'})
            with Image.open(io.BytesIO(blob)) as image:
                for k,v in photo_metadata.extract(image).items():extra.setdefault(k,v)
                image=ImageOps.exif_transpose(image).convert('RGB')
                image.thumbnail((3840,2160),Image.Resampling.LANCZOS)
                temp=target.with_suffix('.tmp');image.save(temp,format='JPEG',quality=93)
                if temp.stat().st_size>16_000_000:
                    temp.unlink();raise ValueError('Prepared image too large')
                temp.chmod(0o600);temp.replace(target)
        if not extra.get('location'):
            try:extra.update(self.places.lookup(extra))
            except (OSError,ValueError,KeyError):pass
        index.extra(key,extra)
        return dict(extra,file='images/'+key+'.jpg',content_key=row.get('content_key'),date=row['date'],
                    date_source='Google Photos Date taken (shared album)')

    def queued(self):
        try:
            data=json.loads(self.manifest.read_text())
            return [r for r in data['photos'] if re.fullmatch(r'images/[a-f0-9]{64}\.jpg',r.get('file','')) and (self.root/r['file']).is_file()]
        except (OSError,ValueError,TypeError,KeyError):return []

    def fill(self,index):
        state=self.snapshot();recent=state.get('photo_recent',[]);current=state.get('photo_key')
        exclusions=Exclusions(self.config)
        all_rows=index.rows(float('inf'))
        blocked={r['key'] for r in all_rows if not exclusions.allows(r)}
        # Exclusions override current-photo retention and the brief cache overlap.
        for key in blocked:
            for suffix in ('.jpg','.tmp'):
                if re.fullmatch(r'[a-f0-9]{64}',key):
                    (self.images/(key+suffix)).unlink(missing_ok=True)
        rows=[r for r in index.rows(self.clock()) if exclusions.allows(r)];by_key={r['key']:r for r in rows}
        queue=[p for p in self.queued() if p['file'][7:-4] in by_key and (p['file']==current or p['file'] not in recent)]
        # Refresh changed dates/captions without downloading existing queued images.
        queue=[dict(json.loads(by_key[p['file'][7:-4]]['extra']),file=p['file'],
                    content_key=by_key[p['file'][7:-4]].get('content_key'),
                    date=by_key[p['file'][7:-4]]['date'],date_source='Google Photos Date taken (shared album)',
                    **({'theme':p['theme']} if p.get('theme') else {})) for p in queue]
        queue=queue[:self.count+1]
        planner=self.themes
        if planner:
            try:
                planner.refresh()
                for receipt in state.get('photo_receipts',[]):
                    planner.record(receipt['id'],receipt['photos'],receipt.get('presentation'))
                rows=planner.plan(rows,state)
                allowed={r['content_key'] for r in rows}
                queue=[r for r in queue if r.get('content_key') in allowed]
            except (OSError,sqlite3.Error,ValueError,KeyError):
                LOG.warning('Photo theme refresh unavailable; ordinary queue retained')
                planner=None
        choices=[{'key':'images/'+r['key']+'.jpg','date':r['date'] or ''} for r in rows]
        planned=copy.deepcopy(state);planned.pop('photo_index',None)
        excluded={i for i,p in enumerate(choices) if any(q['file']==p['key'] for q in queue)}
        for p in queue:
            planned['photo_recent']=(planned.get('photo_recent',[])+[p['file']])[-100:]
            planned['photo_recent_dates']=(planned.get('photo_recent_dates',[])+[p['date']])[-7:]
        target_count=self.count+int(any(q['file']==current for q in queue))
        attempts=0
        while len(queue)<target_count and len(excluded)<len(choices) and attempts<self.count*2:
            i=planner.choose(rows,planned,excluded) if planner else idle_photos.choose(choices,planned,excluded)
            if i is None:break
            excluded.add(i);attempts+=1
            try:
                prepared=self.prepare(rows[i],index)
                if planner:prepared=planner.annotate(prepared)
                used=sum((self.root/q['file']).stat().st_size for q in queue)
                if used+(self.root/prepared['file']).stat().st_size>self.budget:
                    (self.root/prepared['file']).unlink();break
                queue.append(prepared);idle_photos.remember(choices,i,planned)
                self.prune(queue)
                # Publish incrementally so the first image is ready promptly.
                atomic_json(self.manifest,{'schema':1,'photos':queue})
            except Exception as error:
                index.failed(rows[i]['key'],self.clock()+900)
                # Expired image links request one bounded full metadata refresh.
                status=getattr(getattr(error,'response',None),'status_code',None)
                if status in (403,404,410):index.set('link_refresh_needed',True)
                LOG.warning('Photo preparation failed (%s)',type(error).__name__)
        atomic_json(self.manifest,{'schema':1,'photos':queue})
        self.prune(queue)
        return len(rows),len(queue)

    def prune(self,queue):
        keep={Path(r['file']).name for r in queue}
        history=self.themes.history() if self.themes else []
        current=self.snapshot().get('photo_key','')
        if re.fullmatch(r'images/[a-f0-9]{64}\.jpg',current):keep.add(Path(current).name)
        # Brief overlap protects a renderer that already opened the previous manifest.
        history_names={Path(r['file']).name for r in history}
        candidates=[]
        for p in self.images.iterdir():
            if p.is_symlink() or not re.fullmatch(r'[a-f0-9]{64}\.(jpg|tmp)',p.name):continue
            if p.name in keep:continue
            candidates.append(p)
        candidates.sort(key=lambda p:(p.name in history_names,p.stat().st_mtime))
        total=sum(p.stat().st_size for p in self.images.iterdir() if p.is_file())
        count=len(list(self.images.glob('*.jpg')))
        for p in candidates:
            if p.name in history_names and total<=self.budget and count<=28+2*(self.count+1):continue
            if self.clock()-p.stat().st_mtime<60 and total<=self.budget and count<=28*(bool(self.themes))+2*(self.count+1):continue
            size=p.stat().st_size;p.unlink();total-=size;count-=1

    def work(self):
        index=None
        try:
            with (self.root/'worker.lock').open('a') as lockfile:
                try:fcntl.flock(lockfile,fcntl.LOCK_EX|fcntl.LOCK_NB)
                except BlockingIOError:return
                index=Index(self.root/'index.sqlite3');now=self.clock()
                due_full=now-index.setting('last_full')>=7*86400
                due_recent=self.startup or now-index.setting('last_recent')>=4*3600
                due_link=index.setting('link_refresh_needed',False) and now-index.setting('last_link_attempt')>=900
                error=index.setting('last_error',None)
                if (due_full or due_recent or due_link) and now>=index.setting('retry_after'):
                    try:
                        if due_link:index.set('last_link_attempt',now)
                        self.refresh(index,due_full or due_link)
                        index.set('link_refresh_needed',False);self.startup=False
                        index.set('retry_after',0);index.set('last_error',None);error=None
                    except Exception as exc:
                        error=type(exc).__name__;index.set('retry_after',now+900);index.set('last_error',error)
                        LOG.warning('Photo metadata refresh failed (%s); retained index',error)
                manifest=self.config.get('idle_photos_file')
                if manifest and index.rows(float('inf')) and not index.setting('legacy_imported',False):
                    try:
                        index.import_metadata(json.loads(Path(manifest).read_text()))
                        index.set('legacy_imported',True)
                    except (OSError,ValueError):pass
                photos,queued=self.fill(index)
                atomic_json(self.status,{'indexed_photos':len(index.rows(float('inf'))),'queued':queued,'last_full':index.setting('last_full'),
                            'last_recent':index.setting('last_recent'),'refresh_error':error,'checked_at':self.clock(),
                            'cache_bytes':sum(p.stat().st_size for p in self.images.glob('*.jpg'))})
        except Exception as error:
            LOG.warning('Photo worker failed (%s)',type(error).__name__)
        finally:
            if index:index.close()
