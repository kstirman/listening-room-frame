"""Dates with explicit meaning; release/copyright dates are never recording dates."""
import json,re,unicodedata
from pathlib import Path


def years(value):
    value=str(value or '')
    # Preserve explicitly supplied historical periods without inventing years.
    century = r'(?:1st|2nd|3rd|[4-9]th|1[0-9]th|20th|21st)'
    period = r'(?:(?:early|mid|late)\s+)?' + century
    if re.fullmatch(period + r'(?:\s*[–—-]\s*' + period + r')?\s+century(?:\s+or earlier)?', value.strip(), re.I):
        return re.sub(r'\s*[–—-]\s*', '–', re.sub(r'\s+', ' ', value.strip().lower()))
    # Keep historical uncertainty visible instead of turning an estimate into a fact.
    qualifier=re.match(r'^\s*(c\.|ca\.|circa|approximately|probably|before|after|by)\s+',value,re.I)
    if qualifier:
        tail=years(value[qualifier.end():])
        prefix=qualifier.group(1).lower()
        if prefix in ('ca.','circa','approximately'):prefix='c.'
        return prefix+' '+tail if tail else ''
    span=re.search(r'\b((?:1[0-9]|20)\d{2})\s*[–—-]\s*((?:1[0-9]|20)\d{2}|\d{2})\b',value)
    # ISO calendar dates are not year ranges.
    if span and not re.fullmatch(r'\d{4}-(?:0[1-9]|1[0-2])(?:-\d{2})?',value.strip()):
        a,b=span.groups()
        if len(b)==2:b=a[:2]+b
        if int(b)>=int(a):return a+'–'+b if a!=b else a
    found=list(dict.fromkeys(re.findall(r'\b(?:1[0-9]|20)\d{2}\b',value)))
    return ', '.join(found)


def composition_years(value):
    """Keep work labels when a track combines separately dated compositions."""
    value=str(value or '').strip()
    if ':' in value:
        parts=[]
        for item in value.split(';'):
            label,sep,date=item.rpartition(':')
            date=years(date)
            if not sep or not label.strip() or not date:
                return ''
            parts.append(label.strip()+': '+date)
        return '; '.join(parts)
    return years(value)


def composition_fields(metadata, details=None, registry=None):
    """Composition evidence applies to songs and instrumental works alike."""
    explicit=composition_years(metadata.get('compositionDate'))
    if explicit:
        result={'composition_year':explicit}
        if metadata.get('composition_source'):result['composition_source']=metadata['composition_source']
        return result
    if registry is None:
        path=Path(__file__).with_name('work-dates.json')
        registry=json.loads(path.read_text()) if path.exists() else []
    details=details or {}
    writer='; '.join(str(v) for v in [details.get('composer',''),metadata.get('composer',''),metadata.get('songwriters','')])
    work=details.get('work') or metadata.get('work') or metadata.get('title','')
    def key(s):return ' '.join(unicodedata.normalize('NFKC',s).replace('’',"'").casefold().split())
    found=[]
    for entry in registry:
        if not entry.get('source') or not entry.get('composition_year'):continue
        if entry.get('title_match'):
            writers=entry.get('writer_matches',[])
            match=bool(writers and key(metadata.get('title',''))==key(entry['title_match'])
                       and all(key(n) in key(writer) for n in writers))
        else:
            match=bool(entry.get('composer_match') and entry.get('work_pattern')
                       and entry['composer_match'].casefold() in writer.casefold()
                       and re.search(entry['work_pattern'],work,re.I))
        if match:found.append(entry)
    if found and len({e['composition_year'] for e in found})==1:
        return {'composition_year':found[0]['composition_year'],'composition_source':found[0]['source']}
    return {}


def catalog_fields(metadata, registry):
    result=dict(metadata)
    result.pop('composition_year',None);result.pop('composition_date_status',None)
    if not result.get('compositionDate'):result.pop('composition_source',None)
    fields=composition_fields(result,registry=registry)
    result.update(fields)
    result['composition_date_status']='sourced' if fields.get('composition_source') else 'supplied' if fields else 'unknown'
    return result


def extract(metadata, classical=None, work_registry=None, recording_registry=None):
    result={}
    recording=years(metadata.get('recordingDate'))
    if not recording:
        # Only a explicitly labelled live/recorded bracket, never the whole title.
        for block in re.findall(r'\[([^]]+)\]',metadata.get('title','')):
            if re.match(r'(?:Live\b|Recorded\b)',block,re.I) and not re.search('remaster|reissue',block,re.I):
                recording=years(block)
                if recording:break
    if recording:result['recording_year']=recording
    if not recording:
        registry=Path(__file__).with_name('recording-dates.json')
        entries=recording_registry if recording_registry is not None else (json.loads(registry.read_text()) if registry.exists() else {})
        entry=entries.get(metadata.get('object_id',''))
        if entry:
            result['recording_year']=entry['recording_year']
            result['recording_source']=entry['recording_source']
    if not result.get('recording_year'):
        for key, label in [('originalReleaseDate','Originally released'), ('originaldate','Originally released'), ('originalyear','Originally released'), ('releaseDate','Released'), ('date','Released')]:
            value=years(metadata.get(key))
            if value:
                result.update(release_year=value, release_label=label)
                break
    result.update(composition_fields(metadata,classical,work_registry))
    return result


def display_line(details, include_composition=True):
    details=details or {}
    parts=[]
    if include_composition and details.get('composition_year'):parts.append('Composed · '+details['composition_year'])
    if details.get('recording_year'):parts.append('Recorded · '+details['recording_year'])
    elif details.get('release_year'):parts.append(details.get('release_label','Released')+' · '+details['release_year'])
    return '  |  '.join(parts)
