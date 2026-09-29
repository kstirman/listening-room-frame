"""Audit a local inventory and apply reviewed, source-attributed sidecar additions."""
import argparse
import datetime
import json
import shutil
from pathlib import Path

import classical
from library_enrich import first

FIELDS = ('composer', 'work', 'movementName', 'conductor', 'orchestra', 'soloist', 'compositionDate', 'recordingDate')
ALIASES = {'composer': ('composer', 'tcom'), 'work': ('work', 'composition'),
           'movementName': ('movementname',), 'conductor': ('conductor',),
           'orchestra': ('orchestra', 'ensemble'), 'soloist': ('soloist',),
           'compositionDate': ('compositiondate', 'composition_date'),
           'recordingDate': ('recordingdate', 'recording_date', 'recordingyear'),
           'genre': ('genre', 'tcon', '©gen')}

def read(path, default):
    return json.loads(path.read_text()) if path.exists() else default

def audit(root):
    root = root.resolve()
    store = root / '.listening-room-metadata'
    inventory = read(store / 'inventory.json', None)
    if inventory is None:
        raise ValueError('Run library_enrich.py --library-root first')
    tracks = read(store / 'enriched.json', {'tracks': {}})['tracks']
    overrides = read(store / 'track-overrides.json', {})
    results = []
    for album in inventory['albums']:
        for track in album['tracks']:
            path = track['path']
            fields = {key: first(track['tags'], *aliases) for key, aliases in ALIASES.items()}
            fields['title'] = track['title']
            fields.update(tracks.get(path, {}).get('fields', {}))
            fields.update(overrides.get(path, {}).get('fields', {}))
            is_classical = classical.normalize(fields) is not None
            checked = FIELDS if is_classical else ('recordingDate',)
            results.append({'path': path, 'classical': is_classical,
                            'missing_candidates': [key for key in checked if not fields.get(key)],
                            'has_album_art': bool(album.get('cover'))})
    return {'note': 'Missing candidates require review: conductor, movement and ensemble may not apply. Presence is not verification.',
            'tracks': results, 'scan_errors': inventory.get('errors', [])}

def apply(root, proposal):
    """Fill blanks only. Never replace existing values or protected overrides."""
    root = root.resolve()
    store = root / '.listening-room-metadata'
    if not store.is_dir():
        raise ValueError('Scan the library first')
    db = store / 'enriched.json'
    data = read(db, {'albums': [], 'tracks': {}})
    overrides = read(store / 'track-overrides.json', {})
    changes = 0
    for relative, entry in proposal['tracks'].items():
        target = (root / relative).resolve()
        if Path(relative).is_absolute() or not target.is_relative_to(root) or not target.is_file():
            raise ValueError('Proposal must reference existing files beneath the library root')
        fields = entry['fields']
        provenance = entry.get('provenance', {})
        if any(not provenance.get(key) for key in fields):
            raise ValueError('Each proposed field needs provenance')
        saved = data['tracks'].setdefault(relative, {'fields': {}, 'provenance': {}})
        for key, value in fields.items():
            if saved['fields'].get(key) or overrides.get(relative, {}).get('fields', {}).get(key):
                continue
            if value:
                saved['fields'][key] = value
                saved.setdefault('provenance', {})[key] = provenance[key]
                changes += 1
    if changes:
        if db.exists():
            stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
            shutil.copy2(db, store / ('enriched.before-' + stamp + '.json'))
        temp = db.with_suffix('.tmp')
        temp.write_text(json.dumps(data, ensure_ascii=False, indent=2))
        temp.replace(db)
    return changes

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--library-root', required=True, type=Path)
    sub = parser.add_subparsers(dest='action', required=True)
    sub.add_parser('audit')
    command = sub.add_parser('apply')
    command.add_argument('proposal', type=Path)
    args = parser.parse_args()
    root = args.library_root.expanduser().resolve()
    if not root.is_dir(): parser.error('Library root must exist')
    result = audit(root) if args.action == 'audit' else {'fields_added': apply(root, read(args.proposal, {}))}
    print(json.dumps(result, ensure_ascii=False, indent=2))

if __name__ == '__main__':
    main()
