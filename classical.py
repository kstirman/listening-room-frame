"""Conservative source metadata extraction. No inferred performer roles."""
import re,json,subprocess
from pathlib import Path
from urllib.parse import urlparse,unquote
from html.parser import HTMLParser

class Tracks(HTMLParser):
 def __init__(self):
  super().__init__();self.tracks={};self.track=None;self.capture=None;self.parts=[]
 def handle_starttag(self,tag,attrs):
  a=dict(attrs)
  if a.get('data-track'):
   self.track=a['data-track'];self.tracks.setdefault(self.track,{})
  classes=a.get('class','').split()
  if self.track and ('track__item--name' in classes or 'track__info' in classes):
   self.capture=(tag,'title' if 'track__item--name' in classes else 'credits');self.parts=[]
 def handle_data(self,data):
  if self.capture:self.parts.append(data)
 def handle_endtag(self,tag):
  if self.capture and tag==self.capture[0]:
   value=' '.join(''.join(self.parts).split());key=self.capture[1]
   if key=='title':self.tracks[self.track][key]=value
   elif 'Composer' in value or 'Conductor' in value:
    self.tracks[self.track][key]=value
   self.capture=None

def normalize(metadata):
 m=dict(metadata)
 credits=m.get('credits','')
 for person in credits.split(' - '):
  name,sep,roles=person.partition(', ')
  if not sep:continue
  for role,key in [('Composer','composer'),('Conductor','conductor'),('Director','conductor'),('Orchestra','orchestra'),('Soloist','soloist')]:
   if role in [r.strip() for r in roles.split(',')]:
    old=m.get(key,'');m[key]=old+'; '+name if old and name not in old.split('; ') else (old or name)
 title=m.get('title','')
 match=re.match(r'^(.*?):\s*((?:[IVXLCDM]+|\d+)\.\s+.+)$',title)
 if match:
  m.setdefault('work',match[1]);m.setdefault('movementName',match[2])
 genre=str(m.get('genre','')).strip().casefold()
 is_classical=bool(m.get('classical') is True or m.get('work') or 'classical' in genre or genre in {'classique','opera','oper','orchestral','symphony','chamber music','choral','medieval','vocal works'})
 if not is_classical:return None
 work=m.get('work') or ('' if re.fullmatch(r'(?:track[ _-]*\d+|unknown(?: title)?|untitled)',title.strip(),re.I) else title)
 movement=m.get('movementName') or m.get('movement','')
 number=m.get('movementNumber','')
 if number and movement and not re.match(r'^(?:[IVXLCDM]+|\d+)\.',movement):movement=f'{number}. {movement}'
 return {k:v for k,v in {'composer':m.get('composer',''),'work':work,'movement':movement,'conductor':m.get('conductor',''),'orchestra':m.get('orchestra') or m.get('ensemble') or m.get('choir',''),'soloist':m.get('soloist','')}.items() if v}

def file_tags(resource,root,resource_prefix="/minimserver/*/AudirvanaLibrary/"):
 path=urlparse(resource).path;marker=resource_prefix
 if not path.startswith(marker):return {}
 rel=unquote(re.sub(r'\*([0-9a-fA-F]{2})',r'%\1',path[len(marker):]))
 root=Path(root).resolve();file=(root/rel).resolve()
 if not file.is_relative_to(root) or not file.is_file():return {}
 r=subprocess.run(['ffprobe','-v','quiet','-show_entries','format_tags','-of','json',str(file)],capture_output=True,text=True,timeout=8,check=True)
 tags={k.lower():v for k,v in json.loads(r.stdout).get('format',{}).get('tags',{}).items()}
 aliases={'date':'date','year':'date','originaldate':'originalReleaseDate','originalyear':'originalReleaseDate','originalreleasedate':'originalReleaseDate','releasedate':'releaseDate','composer':'composer','conductor':'conductor','orchestra':'orchestra','ensemble':'orchestra','work':'work','composition':'work','movementname':'movementName','movementnumber':'movementNumber','title':'title','genre':'genre','soloist':'soloist','recordingdate':'recordingDate','recording_date':'recordingDate','recordeddate':'recordingDate','recordingyear':'recordingDate','recording_year':'recordingDate','compositiondate':'compositionDate','composition_date':'compositionDate','compositionyear':'compositionDate','composition_year':'compositionDate','composedate':'compositionDate'}
 result={dest:tags[src] for src,dest in aliases.items() if tags.get(src)}
 if tags.get('movement'):
  key='movementNumber' if re.fullmatch(r'\d+(?:/\d+)?',tags['movement']) else 'movementName'
  result.setdefault(key,tags['movement'])
 return result
