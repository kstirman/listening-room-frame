"""Explicit reviewed source-programme selections, never a track composition date."""
from urllib.parse import urlsplit

CAVEAT = 'Source programme; file coverage unconfirmed.'
LABEL = 'PROGRAMME SELECTIONS'
ANNOUNCEMENTS_CAVEAT = 'Works identified in local announcements.'
VERIFIED_ANNOUNCEMENTS = frozenset({
    'verified_local_announcements_three_works_unsegmented',
    'verified_local_announcements_five_works_unsegmented',
})

def caveat(context):
    evidence = context.get('evidence_status')
    return (ANNOUNCEMENTS_CAVEAT if isinstance(evidence, str) and evidence in VERIFIED_ANNOUNCEMENTS
            else CAVEAT)

def extract(fields):
    value = fields.get('programme_context') if isinstance(fields, dict) else None
    if not isinstance(value, dict) or value.get('status') != 'reviewed_source_programme':
        return None
    rows = value.get('rows')
    if not isinstance(rows, list) or not 1 <= len(rows) <= 3:
        return None
    clean = []
    for row in rows:
        if not isinstance(row, dict) or set(row) != {'work', 'year'}:
            return None
        work, year = row['work'], row['year']
        if not all(isinstance(v, str) and v.strip() == v and v and '\n' not in v for v in (work, year)):
            return None
        if len(work + ' · ' + year) > 45:
            return None
        clean.append({'work': work, 'year': year})
    sources = value.get('sources')
    if not isinstance(sources, list) or not sources:
        return None
    for source in sources:
        if not isinstance(source, str):
            return None
        try:
            url = urlsplit(source)
            if url.scheme != 'https' or not url.hostname or url.username or url.password:
                return None
        except ValueError:
            return None
    result = {'status': 'reviewed_source_programme', 'rows': clean, 'sources': list(sources)}
    # Preserve only explicitly reviewed local-announcement evidence through the
    # bridge-to-layout extraction pass. This does not assert complete scores.
    evidence = fields.get('_programme_context_status', value.get('evidence_status'))
    if isinstance(evidence, str) and evidence in VERIFIED_ANNOUNCEMENTS:
        result['evidence_status'] = evidence
    return result
