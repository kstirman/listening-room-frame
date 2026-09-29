"""Read the persistent per-track library enrichment, without network requests."""
import json,re
from pathlib import Path
from urllib.parse import urlparse,unquote
_CACHE={}
def lookup(resource,root,resource_prefix="/minimserver/*/AudirvanaLibrary/"):
 root=Path(root).resolve();path=urlparse(resource).path;marker=resource_prefix
 if not path.startswith(marker):return {}
 rel=unquote(re.sub(r'\*([0-9a-fA-F]{2})',r'%\1',path[len(marker):]))
 target=(root/rel).resolve()
 if not target.is_relative_to(root):return {}
 db=root/'.listening-room-metadata'/'enriched.json'
 overrides=db.with_name('track-overrides.json')
 try:stamp=(db.stat().st_mtime_ns,overrides.stat().st_mtime_ns if overrides.exists() else 0)
 except FileNotFoundError:return {}
 key=str(db)
 if _CACHE.get('key')!=key or _CACHE.get('stamp')!=stamp:
  data=json.loads(db.read_text());tracks=data.get('tracks',{})
  for path,entry in (json.loads(overrides.read_text()) if overrides.exists() else {}).items():
   tracks.setdefault(path,{'fields':{}})['fields'].update(entry['fields'])
  _CACHE.update(key=key,stamp=stamp,tracks=tracks)
 return dict(_CACHE['tracks'].get(str(target.relative_to(root)),{}).get('fields',{}))
