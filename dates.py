"""Dates with explicit meaning; release/copyright dates are never recording dates."""
import json,re
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


def extract(metadata, classical=None):
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
        entry=json.loads(registry.read_text()).get(metadata.get('object_id','')) if registry.exists() else None
        if entry:
            result['recording_year']=entry['recording_year']
            result['recording_source']=entry['recording_source']
    if not result.get('recording_year'):
        for key, label in [('originalReleaseDate','Originally released'), ('originaldate','Originally released'), ('originalyear','Originally released'), ('releaseDate','Released'), ('date','Released')]:
            value=years(metadata.get(key))
            if value:
                result.update(release_year=value, release_label=label)
                break
    if classical:
        composition=composition_years(metadata.get('compositionDate'))
        if composition:result['composition_year']=composition
        else:
            catalog=Path(__file__).with_name('work-dates.json')
            for entry in json.loads(catalog.read_text()) if catalog.exists() else []:
                if entry['composer_match'].casefold() in classical.get('composer','').casefold() and re.search(entry['work_pattern'],classical.get('work',''),re.I):
                    result['composition_year']=entry['composition_year']
                    result['composition_source']=entry['source']
                    break
    return result


def display_line(details):
    details=details or {}
    if details.get('recording_year'):return 'Recorded · '+details['recording_year']
    if details.get('release_year'):return details.get('release_label','Released')+' · '+details['release_year']
    return ''
