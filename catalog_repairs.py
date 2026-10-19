"""Apply sourced, reviewed corrections only to an exact complete track sequence."""
import timing

FIELDS={'composer','conductor','orchestra','work','movementName','movementNumber',
        'compositionDate','composition_source'}

def apply(rows, repairs):
    result={k:dict(v) for k,v in rows.items()}
    if not isinstance(repairs,list):return result
    for group in repairs:
        if not isinstance(group,dict) or group.get('review_status')!='reviewed' or not group.get('source'):continue
        targets=group.get('tracks',[]);album=group.get('album_id')
        if not album or not isinstance(targets,list) or not 2<=len(targets)<=128:continue
        ids=[t.get('id') for t in targets if isinstance(t,dict)]
        if len(ids)!=len(targets) or any(not isinstance(k,str) for k in ids) or len(set(ids))!=len(ids) or any(k not in result for k in ids):continue
        order=list(result);start=order.index(ids[0])
        if order[start:start+len(ids)]!=ids:continue
        valid=True
        for t in targets:
            r=result[t['id']];duration=timing.seconds(t.get('duration_seconds'))
            if (r.get('_catalog_album_id')!=album or r.get('title')!=t.get('title')
                    or not duration or timing.seconds(r.get('duration_seconds'))!=duration
                    or not t.get('credits') or r.get('credits')!=t['credits']
                    or not isinstance(t.get('fields'),dict)):
                valid=False;break
        if not valid:continue
        for t in targets:
            r=result[t['id']]
            r.update({k:v for k,v in t['fields'].items() if k in FIELDS and isinstance(v,str) and v})
            r['work_scope']='reviewed:'+album+':'+ids[0]
            r['_reviewed_catalog_source']=group['source']
    return result
