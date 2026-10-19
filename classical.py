"""Conservative source metadata extraction. No inferred performer roles."""
import re,json,subprocess
import timing
from pathlib import Path
from urllib.parse import urlparse,unquote
from html.parser import HTMLParser

def tempo_section(title):
 """Recognize bare tempo directions, never independent named compositions."""
 starts = r'(?:largo|larghetto|lento|adagio|adagietto|andante|andantino|moderato|allegretto|allegro|vivace|vivacissimo|presto|prestissimo|grave|grandioso|recitativo)'
 words = starts + r'|assai|molto|poco|più|piu|meno|non|troppo|sostenuto|energico|maestoso|cantabile|espressivo|con|brio|fuoco|moto|mosso|e|ma|dolce|tranquillo|agitato|appassionato|quasi|tempo|primo|giusto|rubato|sempre|ben|marcato|ritenuto'
 return bool(re.fullmatch(starts + r'(?:[\s.,;:()–—-]+(?:' + words + r'))*[\s.,;:()–—-]*', title, re.I))


class Tracks(HTMLParser):
 def __init__(self):
  super().__init__();self.tracks={};self.track=None;self.capture=None;self.parts=[]
  self.depth=0;self.work='';self.group_depth=None;self.work_scope=0
  self.catalog_heading='';self.catalog_disc=0
 def handle_starttag(self,tag,attrs):
  if tag in {'area','base','br','col','embed','hr','img','input','link','meta','param','source','track','wbr'}:return
  self.depth+=1
  a=dict(attrs);classes=a.get('class','').split()
  if a.get('id')=='playerTracks':
   self.work='';self.track=None;self.group_depth=self.depth
   self.catalog_heading='';self.catalog_disc=0
  if a.get('data-track'):
   self.track=a['data-track'];self.tracks.setdefault(self.track,{})
   try:
    category=json.loads(a.get('data-gtm','{}')).get('product',{}).get('category')
    if isinstance(category,str):self.tracks[self.track]['genre']=category
   except (ValueError,AttributeError):pass
  if 'player__work' in classes:
   self.work_scope+=1
   if 'data-disk' in a:
    self.work=''
    self.catalog_heading='';self.catalog_disc+=1
   else:
    self.capture=(self.depth,'work');self.parts=[]
  elif self.track and 'track__item--duration' in classes:
   self.capture=(self.depth,'duration');self.parts=[]
  elif self.track and ('track__item--name' in classes or 'track__info' in classes):
   self.capture=(self.depth,'title' if 'track__item--name' in classes else 'credits');self.parts=[]
 def handle_data(self,data):
  if self.capture:self.parts.append(data)
 def handle_endtag(self,tag):
  if tag in {'area','base','br','col','embed','hr','img','input','link','meta','param','source','track','wbr'}:return
  if self.capture and self.depth==self.capture[0]:
   value=' '.join(''.join(self.parts).split());key=self.capture[1]
   if key=='work':self.work=value;self.catalog_heading=value
   elif key=='duration':
    duration=timing.seconds(value)
    if duration:self.tracks[self.track]['duration_seconds']=duration
   elif key=='title':
    self.tracks[self.track][key]=value
    if self.catalog_heading:
     self.tracks[self.track].update(catalog_heading=self.catalog_heading,catalog_disc=self.catalog_disc)
    # Explicit headings can describe numbered movements or bare tempo sections.
    # An independent title ends inheritance, so later tracks cannot borrow it.
    if self.work and (timing.number({'title':value}) or tempo_section(value)):
     self.tracks[self.track].update(work=self.work,movementName=value,work_scope=self.work_scope,part_group=True)
    else:
     self.work=''
   elif key=='credits':
    # A track has separate personnel and copyright info blocks. Preserve all
    # blocks so a trailing rights notice cannot erase its credited performers.
    old=self.tracks[self.track].get(key,'')
    self.tracks[self.track][key]=' - '.join(dict.fromkeys(v for v in (old,value) if v))
   self.capture=None
  if self.depth==self.group_depth:
   self.work='';self.track=None;self.group_depth=None
  self.depth=max(0,self.depth-1)

def normalize(metadata):
 m=dict(metadata)
 credits=m.get('credits','')
 for person in credits.split(' - '):
  name,sep,roles=person.partition(', ')
  if not sep:continue
  for role,key in [('Composer','composer'),('Conductor','conductor'),('Director','conductor'),('Orchestra','orchestra'),('Ensemble','orchestra'),('Soloist','soloist'),('MainArtist','performer'),('AssociatedPerformer','performer')]:
   if role in [r.strip() for r in roles.split(',')]:
    old=m.get(key,'');m[key]=old+'; '+name if old and name not in old.split('; ') else (old or name)
 title=m.get('title','')
 match=re.match(r'^(.*?):\s*((?:[IVXLCDM]+|\d+)\.\s+.+)$',title)
 if match:
  m.setdefault('work',match[1]);m.setdefault('movementName',match[2])
 genre=str(m.get('genre','')).strip().casefold()
 is_classical=bool(m.get('classical') is True or m.get('work') or 'classical' in genre or genre in {'classique','opera','oper','orchestral','symphony','chamber music','choral','medieval','vocal works'})
 if not is_classical:return None
 # Preserve a known part even when the source provides no complete album catalog.
 if not m.get('movementName'):
  named=re.match(r'^(.*):\s*(No\.\s*\d+(?:[\s,.:].*)?)$',title)
  if named:
   m['work']=named[1];m['movementName']=named[2]

 work=m.get('work') or ('' if re.fullmatch(r'(?:track[ _-]*\d+|unknown(?: title)?|untitled)',title.strip(),re.I) else title)
 movement=m.get('movementName') or m.get('movement','')
 number=m.get('movementNumber','')
 if number and movement and not re.match(r'^(?:[IVXLCDM]+|\d+)\.',movement):movement=f'{number}. {movement}'
 # Keep explicitly credited performers without guessing instruments or roles.
 known={name for field in ('composer','conductor','orchestra','ensemble','choir','soloist') for name in m.get(field,'').split('; ') if name}
 performers='; '.join(dict.fromkeys(name for name in m.get('performer','').split('; ') if name and name not in known))
 return {k:v for k,v in {'composer':m.get('composer',''),'work':work,'movement':movement,'conductor':m.get('conductor',''),'orchestra':m.get('orchestra') or m.get('ensemble') or m.get('choir',''),'soloist':m.get('soloist',''),'performer':performers}.items() if v}

def file_tags(resource,root,resource_prefix="/minimserver/*/AudirvanaLibrary/"):
 path=urlparse(resource).path;marker=resource_prefix
 if not path.startswith(marker):return {}
 rel=unquote(re.sub(r'\*([0-9a-fA-F]{2})',r'%\1',path[len(marker):]))
 root=Path(root).resolve();file=(root/rel).resolve()
 if not file.is_relative_to(root) or not file.is_file():return {}
 r=subprocess.run(['ffprobe','-v','quiet','-show_entries','format=duration:format_tags','-of','json',str(file)],capture_output=True,text=True,timeout=8,check=True)
 data=json.loads(r.stdout).get('format',{})
 tags={k.lower():v for k,v in data.get('tags',{}).items()}
 aliases={'date':'date','year':'date','originaldate':'originalReleaseDate','originalyear':'originalReleaseDate','originalreleasedate':'originalReleaseDate','releasedate':'releaseDate','composer':'composer','conductor':'conductor','orchestra':'orchestra','ensemble':'orchestra','work':'work','composition':'work','movementname':'movementName','movementnumber':'movementNumber','title':'title','genre':'genre','soloist':'soloist','recordingdate':'recordingDate','recording_date':'recordingDate','recordeddate':'recordingDate','recordingyear':'recordingDate','recording_year':'recordingDate','compositiondate':'compositionDate','composition_date':'compositionDate','compositionyear':'compositionDate','composition_year':'compositionDate','composedate':'compositionDate'}
 result={dest:tags[src] for src,dest in aliases.items() if tags.get(src)}
 duration=timing.seconds(data.get('duration'))
 if duration:result['duration_seconds']=duration
 if tags.get('movement'):
  key='movementNumber' if re.fullmatch(r'\d+(?:/\d+)?',tags['movement']) else 'movementName'
  result.setdefault(key,tags['movement'])
 return result


def catalog(tracks):
 """Normalize catalog rows; recognize explicit shared classical work prefixes.

 Only adjacent rows with the same composer and exact 'Work: section' prefix
 provide evidence for unnumbered parts. A colon on one title is insufficient.
 """
 ordered={}
 for key,item in tracks.items():
  row=dict(item);details=normalize(item) or {}
  if details.get('composer'):row['composer']=details['composer']
  if details.get('work') and details.get('movement'):
   row.update(work=details['work'],movementName=details['movement'])
  ordered[key]=row
 entries=list(ordered.items())
 def parts(row):
  title=row.get('title','')
  # A composer prefix is not a work. Require an exact credited full/last name.
  head,sep,rest=title.partition(': ')
  composer=row.get('composer','')
  if sep and composer and head.casefold() in {composer.casefold(),composer.split()[-1].casefold()}:
   title=rest
  work,sep,section=title.partition(': ')
  act=re.fullmatch(r'(.+?)(?: / |, )((?:Act|Scene) [IVX0-9]+)',work)
  if act:
   work=act[1];section=act[2]+': '+section
  return work,sep,section
 # Repair one catalog-number omission only when both immediate neighbors
 # agree on the exact work/composer/scope and the same act. Never use album
 # title similarity to combine unrelated pieces or alternate performances.
 parsed=[parts(row) for _,row in entries]
 aliases={}
 def act_number(section):
  match=re.match(r'Act ([IVX0-9]+):',section)
  if not match:return None
  romans=['I','II','III','IV','V','VI','VII','VIII','IX','X']
  return int(match[1]) if match[1].isdigit() else romans.index(match[1])+1 if match[1] in romans else None
 for i in range(1,len(entries)-1):
  row=entries[i][1];left=entries[i-1][1];right=entries[i+1][1]
  work,sep,section=parsed[i];lw,ls,lp=parsed[i-1];rw,rs,rp=parsed[i+1]
  base=re.sub(r', (?:WWV|BWV|K\.?|Op\.?)\s*\d+[a-z]?', '', lw)
  if (sep and ls and rs and lw==rw and base!=lw and work==base
      and row.get('composer') and row.get('composer')==left.get('composer')==right.get('composer')
      and row.get('work_scope')==left.get('work_scope')==right.get('work_scope')
      and act_number(section) is not None and act_number(section)==act_number(lp)==act_number(rp)):
   aliases[id(row)]=(lw,sep,section)
 original_parts=parts
 def parts(row):
  return aliases.get(id(row),original_parts(row))
 def prefix(row):
  if not normalize(row) or not row.get('composer'):return None
  work,sep,section=parts(row)
  return (work,row['composer'],row.get('work_scope')) if sep and work.strip() and section.strip() else None
 start=0
 while start<len(entries):
  identity=prefix(entries[start][1]);end=start+1
  while identity and end<len(entries) and prefix(entries[end][1])==identity:end+=1
  if identity and end-start>=2:
   for _,row in entries[start:end]:
    work,_,section=parts(row)
    row.update(work=work,movementName=section,part_group=True)
  start=end
 return interrupted_works(ordered)


def interrupted_works(ordered):
 """Bridge explicitly labeled audience interludes in a numbered performance.

 Require a source work heading, identical credited performers/composer, same
 disc and consecutive movement numbers starting at one. Never infer a work
 from an album name, skip arbitrary tracks, or join repeated performances.
 """
 group=[];gaps=[];identity=None;saw_gap=False
 def finish():
  if saw_gap and len(group)>=2:
   scope='interrupted:'+group[0][0]
   for _,row,work,movement in group:
    row.update(work=work,movementName=movement,work_scope=scope,part_group=True)
   for row in gaps:row['non_music_interlude']=True
 def nonmusic(row):
  return (re.fullmatch(r"(?:Audience(?:'s)? noise|Applause)(?: - .+)?",row.get('title',''),re.I)
          and timing.seconds(row.get('duration_seconds'))
          and timing.seconds(row['duration_seconds'])<=120
          and row.get('composer','').casefold() in {'','na not applicable','not applicable','unknown'})
 for key,row in ordered.items():
  if nonmusic(row):
   if group:gaps.append(row);saw_gap=True
   continue
  composer=row.get('composer','');heading=row.get('catalog_heading','')
  suffix=' ('+composer+')'
  if composer and heading.endswith(suffix):heading=heading[:-len(suffix)]
  movement=row.get('title','');work=heading
  # Some catalogs put the movement's number/name in the work heading and
  # expose only its tempo as the track title. Preserve that original title.
  split=re.fullmatch(r'(.+), ((?:[IVX]+|\d+)\. .+)',heading)
  if split and tempo_section(movement):
   work=split[1];movement=split[2]+': '+movement
  n=timing.number({'title':movement})
  ident=(work,composer,row.get('credits',''),row.get('catalog_disc'))
  valid=bool(heading and composer and row.get('credits') and n and timing.seconds(row.get('duration_seconds')))
  if valid and group and ident==identity and n==len(group)+1:
   group.append((key,row,work,movement));continue
  finish();group=[];gaps=[];identity=None;saw_gap=False
  if valid and n==1:
   group=[(key,row,work,movement)];identity=ident
 finish()
 return ordered
