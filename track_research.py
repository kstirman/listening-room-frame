"""Source and retain credits and musical relationships at recording scope."""
import hashlib
import json
import logging
import re
import threading
import time
from queue import Queue

import requests
from persistent_metadata import canonical, merge_missing

LOG = logging.getLogger(__name__)


def identity(metadata):
    album = metadata.get('album_id', '')
    track = metadata.get('object_id', '')
    if re.fullmatch(r'[A-Za-z0-9]{1,64}', album) and re.fullmatch(r'qobuz/track/\d+', track):
        return album + '/' + track
    if not album and metadata.get('resource'):
        return 'local/'+hashlib.sha256(metadata['resource'].split('?')[0].encode()).hexdigest()
    return None


def credit_names(credit):
    return [canonical(row.get('name') or (row.get('artist') or {}).get('name')) for row in credit]


def recording_match(response, fields):
    rows = response.get('recordings', [])
    if int(response.get('count', len(rows))) != len(rows):
        return None
    matched = set()
    for row in rows:
        if (canonical(row.get('title')) == canonical(fields['title'])
                and canonical(fields['artist']) in credit_names(row.get('artist-credit', []))
                and abs(float(row.get('length') or 0)/1000 - fields['duration_seconds']) <= 1
                and any(canonical(r.get('title')) == canonical(fields['album']) for r in row.get('releases', []))
                and re.fullmatch(r'[0-9a-f-]{36}', row.get('id', ''))):
            matched.add(row['id'])
    return matched.pop() if len(matched) == 1 else None


def recording_facts(recording):
    people, producers, engineers, writers, notes = [], [], [], [], []
    for rel in recording.get('relations', []):
        role = rel.get('type')
        artist = (rel.get('artist') or {}).get('name')
        if artist and role in ('vocal','instrument','performer'):
            people.append({'name':artist,'role':', '.join(rel.get('attributes') or [role])})
        if artist and role == 'producer':producers.append(artist)
        if artist and role in ('engineer','mix','recording'):engineers.append(artist)
        work = rel.get('work') or {}
        if role == 'performance' and work.get('title'):
            for credit in work.get('relations', []):
                name = (credit.get('artist') or {}).get('name')
                if name and credit.get('type') in ('composer','lyricist','writer'):
                    writers.append(name)
            if 'cover' in rel.get('attributes', []):
                notes.append('A cover performance of “'+work['title']+'”.')
            elif canonical(work['title']) != canonical(recording.get('title')):
                notes.append('This recording performs “'+work['title']+'”.')
        target = (rel.get('recording') or {}).get('title')
        if target and rel.get('direction') == 'forward' and role == 'samples material':
            notes.append('Samples material from “'+target+'”.')
    if not notes and writers:
        notes.append('The underlying work is credited to '+', '.join(dict.fromkeys(writers))+'.')
    facts = {'personnel':people,'producer':'; '.join(dict.fromkeys(producers)),
             'engineer':'; '.join(dict.fromkeys(engineers)), 'songwriters':'; '.join(dict.fromkeys(writers)),
             'note':next(iter(notes),'')}
    return {k:v for k,v in facts.items() if v}


class Research:
    def __init__(self, cache, provider):
        self.cache = cache
        self.provider = provider
        self.pending = set()
        self.queue = Queue()
        self.lock = threading.Lock()
        self.worker = None

    def lookup(self, metadata, fields, need_credits, need_context, qobuz=None, musicbrainz=True):
        key = identity(metadata)
        if not key:return {}
        saved = self.cache.get('recording-research',key) or {}
        facts = saved.get('fields',{})
        if saved.get('complete') or not (need_credits or need_context):
            return facts
        if not all(fields.get(k) for k in ('title','album','artist','duration_seconds')):
            return facts
        if self.cache.cooling_down('recording-research',key,3600):return facts
        with self.lock:
            if key not in self.pending:
                self.pending.add(key)
                self.queue.put((key,dict(metadata),dict(fields),qobuz,musicbrainz))
                if self.worker is None:
                    self.worker = threading.Thread(target=self.run,daemon=True)
                    self.worker.start()
        return facts

    def run(self):
        while True:
            job = self.queue.get()
            try:self.research(*job)
            except Exception as error:
                status = getattr(getattr(error,'response',None),'status_code',None)
                if status in (401,403,429):
                    try:self.cache.write([],('musicbrainz','__access__',str(status)))
                    except Exception:pass
                LOG.warning('Recording research deferred: %s',type(error).__name__)
                try:self.cache.write([],('recording-research',job[0],'temporarily unavailable'))
                except Exception:LOG.warning('Recording retry could not be saved')
            finally:
                with self.lock:self.pending.discard(job[0])
                self.queue.task_done()

    def research(self,key,metadata,fields,qobuz,musicbrainz):
        facts, sources = {}, []
        if qobuz and metadata.get('album_id'):
            enriched = qobuz(metadata, fields, research=True)
            facts = {k:v for k,v in enriched.items() if k in
                     ('credits','personnel','producer','engineer','songwriters','note') and v and v!=fields.get(k)}
            if facts:
                sources.append('https://www.qobuz.com/us-en/album/-/'+metadata['album_id'])
                self.cache.write([('recording-research',key,{'fields':facts,'qobuz_source':sources[0]})])
        if musicbrainz:
            if self.cache.attempted('musicbrainz','__access__'):
                return
            def quoted(value):
                return '"'+re.sub(r'([\\"+\-!(){}\[\]^~*?:/])',r'\\\1',value)+'"'
            query = ' AND '.join(k+':'+quoted(fields[v]) for k,v in
                                 [('recording','title'),('artist','artist'),('release','album')])
            response = self.provider.request('recording',{'query':query,'limit':20})
            rid = recording_match(response,fields)
            if rid:
                recording=self.provider.request('recording/'+rid,{'inc':'artist-rels+work-rels+recording-rels+work-level-rels'})
                if recording.get('id')==rid:
                    facts=merge_missing(facts,recording_facts(recording))
                    sources.append('https://musicbrainz.org/recording/'+rid)
        self.cache.write([('recording-research',key,{'fields':facts,'sources':sources,'complete':True})])
