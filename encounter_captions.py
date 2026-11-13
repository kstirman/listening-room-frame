"""Draft album-only fallback; not a verified edition or track resolver."""
import json
import re
import sqlite3
from urllib.parse import urlparse

def encountered_album(db, metadata):
    album_id = metadata.get('album_id')
    if metadata.get('source') not in (None, '', 'qobuz'):
        return {}
    if not isinstance(album_id, str) or not re.fullmatch(r'[A-Za-z0-9]{1,64}', album_id):
        return {}
    try:
        row = db.execute('SELECT album_id,payload FROM encountered_albums WHERE identity=?',
                         ('qobuz:' + album_id,)).fetchone()
        if not row or row[0] != album_id:
            return {}
        payload = json.loads(row[1])
        if not isinstance(payload, dict) or payload.get('album_id') != album_id:
            return {}
        provenance = payload.get('_album_identity_source', '')
        reviewed = (isinstance(provenance, str) and
                    urlparse(provenance).scheme == 'https' and
                    bool(payload.get('_album_identity_evidence')))
        if payload.get('source') != 'qobuz' and not reviewed:
            return {}
        allowed = payload.get('_album_identity_fields')
        if not isinstance(allowed, list):
            allowed = ['album'] if payload.get('source') == 'qobuz' else []
        allowed = set(allowed) & {'album', 'albumArtist', 'artist'}
        # Display saved album-level labels only. No dates, composer, title or
        # duration leak from an intake candidate into track identification.
        result = {k: payload[k].strip() for k in ('album', 'albumArtist', 'artist')
                  if k in allowed and isinstance(payload.get(k), str) and payload[k].strip()
                  and payload[k].strip() != album_id
                  and not payload[k].strip().lower().startswith(('qobuz:', 'qobuz/', 'http://', 'https://'))}
        # Upstream nonempty fields win over a historical encounter.
        for k in tuple(result):
            if metadata.get(k) or (k in ('artist', 'albumArtist') and
                                   (metadata.get('artist') or metadata.get('albumArtist'))):
                result.pop(k)
        return result
    except (ValueError, sqlite3.Error):
        return {}
