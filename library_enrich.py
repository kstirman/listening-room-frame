"""Read-only audio inventory plus reversible artwork sidecars. Never edits audio."""
import json,sys,time,re,hashlib,io,os,unicodedata
from pathlib import Path
import mutagen,requests
from PIL import Image
ROOT=Path(os.environ.get('FRAME_LIBRARY_ROOT', '.')).expanduser().resolve()
STORE=ROOT/'.listening-room-metadata'
CACHE=STORE/'http-cache'
ART=STORE/'covers'
SESSION=requests.Session();SESSION.headers['User-Agent']='ListeningRoomMetadata/1.0 (private music library enrichment)'
last=0

def init_storage():
 for p in (STORE,CACHE,ART):p.mkdir(exist_ok=True)

def get(url):
 global last
 init_storage()
 f=CACHE/(hashlib.sha256(url.encode()).hexdigest()+'.json')
 if f.exists():return json.loads(f.read_text())
 if 'musicbrainz.org' in url:time.sleep(max(0,1.15-(time.monotonic()-last)));last=time.monotonic()
 for attempt in range(3):
  r=SESSION.get(url,timeout=25)
  if r.status_code not in (429,502,503,504):break
  time.sleep(3*(attempt+1));last=time.monotonic()
 r.raise_for_status();data=r.json();f.write_text(json.dumps(data));return data

def first(t,*names):
 for n in names:
  v=t.get(n.lower())
  if v:return str(v[0] if isinstance(v,list) else v)
 return ''
def clean(s):return re.sub(r'[^\w]+','', ''.join(c for c in unicodedata.normalize('NFKD',s.lower()) if not unicodedata.combining(c)))
def coverbytes(m):
 if getattr(m,'pictures',None):return m.pictures[0].data
 t=m.tags or {}
 for k,v in t.items():
  if k.startswith('APIC'):return v.data
 if t.get('covr'):return bytes(t['covr'][0])
 return None

def jpeg(data):
 im=Image.open(io.BytesIO(data));im.thumbnail((1600,1600));b=io.BytesIO();im.convert('RGB').save(b,'JPEG',quality=92);return b.getvalue()
def save_new(path,data):
 try:
  with path.open('xb') as f:f.write(data)
  return True
 except FileExistsError:return False

def scan():
 init_storage()
 albums={};errors=[]
 for p in sorted(ROOT.rglob('*')):
  if p.name.startswith('._'):continue
  if p.suffix.lower() not in {'.flac','.aif','.aiff','.wav','.m4a','.mp3'}:continue
  try:
   m=mutagen.File(p);raw={str(k).lower():[str(x) for x in v] if isinstance(v,list) else str(v) for k,v in (m.tags or {}).items() if not str(k).startswith('APIC') and k not in ('covr','metadata_block_picture')}
   title=first(raw,'title','tit2','©nam') or p.stem;album=first(raw,'album','talb','©alb');artist=first(raw,'albumartist','album artist','tpe2','aart') or first(raw,'artist','tpe1','©art')
   key=str(p.parent.relative_to(ROOT))+'|'+album
   a=albums.setdefault(key,{'directory':str(p.parent.relative_to(ROOT)),'album':album,'artist':artist,'tracks':[]})
   pic=coverbytes(m);cf=ART/(hashlib.sha256(key.encode()).hexdigest()+'.jpg')
   if pic and not cf.exists():cf.write_bytes(jpeg(pic))
   if cf.exists():a['cover']=str(cf);a['cover_source']='embedded image in this album'
   t={'path':str(p.relative_to(ROOT)),'title':title,'tags':raw,'duration':round(m.info.length,2),'embedded_art':bool(pic)};a['tracks'].append(t)
   if len(a['tracks'])==1:
    names=[re.sub(r'["*/:<>?\\|]','',album),'folder','cover',p.stem]
    for name in names:
     for ext in ('.jpg','.png','.webp','.JPG','.PNG'):
      f=p.parent/(name+ext)
      if f.is_file():
       a['cover']=str(f);a['cover_source']='existing local image';break
     if a.get('cover_source')=='existing local image':break
  except Exception as e:errors.append({'path':str(p),'error':str(e)})
 out={'albums':list(albums.values()),'errors':errors};(STORE/'inventory.json').write_text(json.dumps(out,ensure_ascii=False,indent=2));print(json.dumps({'albums':len(albums),'tracks':sum(len(a['tracks']) for a in albums.values()),'albums_with_local_cover':sum(bool(a.get('cover')) for a in albums.values()),'errors':errors[:10]}),flush=True)
 return out

if __name__=='__main__':
 import argparse
 parser=argparse.ArgumentParser(description=__doc__)
 parser.add_argument('--library-root',required=True,type=Path)
 args=parser.parse_args()
 ROOT=args.library_root.expanduser().resolve()
 if not ROOT.is_dir():parser.error('Library root must be an existing directory')
 STORE=ROOT/'.listening-room-metadata';CACHE=STORE/'http-cache';ART=STORE/'covers'
 scan()
