"""Read-only audio inventory plus reversible artwork sidecars. Never edits audio."""
import json,sys,time,re,hashlib,io,os,stat,unicodedata
from pathlib import Path
import mutagen,requests
from cover_images import normalized_cover
ROOT=Path(os.environ.get('FRAME_LIBRARY_ROOT', '.')).expanduser().resolve()
STORE=ROOT/'.listening-room-metadata'
CACHE=STORE/'http-cache'
ART=STORE/'covers'
SESSION=requests.Session();SESSION.headers['User-Agent']='ListeningRoomMetadata/1.0 (private music library enrichment)'
last=0
PRIVATE_DIRECTORY_MODE=0o700
PRIVATE_FILE_MODE=0o600

def _require_owner(info,path):
 if info.st_uid != os.getuid():raise PermissionError(f'Metadata artifact is not owned by the current user: {path}')

def private_directory(path):
 path=Path(path);path.mkdir(mode=PRIVATE_DIRECTORY_MODE,exist_ok=True)
 flags=os.O_RDONLY|getattr(os,'O_DIRECTORY',0)|getattr(os,'O_NOFOLLOW',0)
 try:fd=os.open(path,flags)
 except OSError as e:raise PermissionError(f'Metadata directory must not be a symbolic link: {path}') from e
 try:
  info=os.fstat(fd);_require_owner(info,path)
  if not stat.S_ISDIR(info.st_mode):raise PermissionError(f'Metadata path is not a directory: {path}')
  os.fchmod(fd,PRIVATE_DIRECTORY_MODE)
 finally:os.close(fd)

def private_file(path):
 path=Path(path)
 flags=os.O_RDONLY|getattr(os,'O_NOFOLLOW',0)
 try:fd=os.open(path,flags)
 except OSError as e:raise PermissionError(f'Metadata file must not be a symbolic link: {path}') from e
 try:
  info=os.fstat(fd);_require_owner(info,path)
  if not stat.S_ISREG(info.st_mode):raise PermissionError(f'Metadata path is not a regular file: {path}')
  os.fchmod(fd,PRIVATE_FILE_MODE)
 finally:os.close(fd)

def secure_storage(store):
 """Restrict an existing metadata tree after verifying every artifact owner."""
 private_directory(store)
 for path in sorted(Path(store).rglob('*'),key=lambda item:len(item.parts)):
  info=path.lstat()
  if stat.S_ISLNK(info.st_mode):raise PermissionError(f'Metadata artifacts must not be symbolic links: {path}')
  (private_directory if stat.S_ISDIR(info.st_mode) else private_file)(path)

def _private_writer(path,exclusive=False):
 path=Path(path)
 flags=os.O_WRONLY|os.O_CREAT|getattr(os,'O_NOFOLLOW',0)
 if exclusive:flags|=os.O_EXCL
 fd=os.open(path,flags,PRIVATE_FILE_MODE)
 try:
  info=os.fstat(fd);_require_owner(info,path)
  if not stat.S_ISREG(info.st_mode):raise PermissionError(f'Metadata path is not a regular file: {path}')
  os.fchmod(fd,PRIVATE_FILE_MODE)
  if not exclusive:os.ftruncate(fd,0)
  return fd
 except Exception:
  os.close(fd);raise

def private_write_text(path,data):
 fd=_private_writer(path)
 with os.fdopen(fd,'w',encoding='utf-8') as stream:stream.write(data)

def private_write_bytes(path,data,exclusive=False):
 fd=_private_writer(path,exclusive=exclusive)
 with os.fdopen(fd,'wb') as stream:stream.write(data)

def private_copy(source,target):
 fd=_private_writer(target,exclusive=True)
 try:
  with os.fdopen(fd,'wb') as writer,Path(source).open('rb') as reader:
   while chunk:=reader.read(1024*1024):writer.write(chunk)
 except Exception:
  Path(target).unlink(missing_ok=True);raise

def init_storage():
 for p in (STORE,CACHE,ART):private_directory(p)
 secure_storage(STORE)

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
 r.raise_for_status();data=r.json();private_write_text(f,json.dumps(data));return data

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
 with normalized_cover(io.BytesIO(data)) as image:
  b=io.BytesIO();image.save(b,'JPEG',quality=92);return b.getvalue()
def save_new(path,data):
 try:
  private_write_bytes(path,data,exclusive=True)
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
   if pic and not cf.exists():save_new(cf,jpeg(pic))
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
 out={'albums':list(albums.values()),'errors':errors};private_write_text(STORE/'inventory.json',json.dumps(out,ensure_ascii=False,indent=2));print(json.dumps({'albums':len(albums),'tracks':sum(len(a['tracks']) for a in albums.values()),'albums_with_local_cover':sum(bool(a.get('cover')) for a in albums.values()),'errors':errors[:10]}),flush=True)
 return out

if __name__=='__main__':
 import argparse
 os.umask(0o077)
 parser=argparse.ArgumentParser(description=__doc__)
 parser.add_argument('--library-root',required=True,type=Path)
 args=parser.parse_args()
 ROOT=args.library_root.expanduser().resolve()
 if not ROOT.is_dir():parser.error('Library root must be an existing directory')
 STORE=ROOT/'.listening-room-metadata';CACHE=STORE/'http-cache';ART=STORE/'covers'
 scan()
