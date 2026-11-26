"""Audit a local inventory and apply reviewed, source-attributed sidecar additions."""
import argparse
import datetime
import json
import os
from pathlib import Path

import classical
from library_enrich import first
from metadata_fs import (harden_private_tree, copy_file_exclusive_at, open_private_directory, open_root,
                         read_json_at, regular_file_exists_at,
                         require_regular_beneath, write_json_atomic_at)

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
    with open_root(root) as root_fd:
        with open_private_directory(
                root_fd, '.listening-room-metadata',
                missing_message='Run library_enrich.py --library-root first') as store_fd:
            harden_private_tree(store_fd)
            inventory = read_json_at(store_fd, 'inventory.json', None)
            if inventory is None:
                raise ValueError('Run library_enrich.py --library-root first')
            tracks = read_json_at(store_fd, 'enriched.json', {'tracks': {}})['tracks']
            overrides = read_json_at(store_fd, 'track-overrides.json', {})
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
    with open_root(root) as root_fd:
        with open_private_directory(
                root_fd, '.listening-room-metadata',
                missing_message='Scan the library first') as store_fd:
            harden_private_tree(store_fd)
            db_exists = regular_file_exists_at(store_fd, 'enriched.json')
            data = read_json_at(store_fd, 'enriched.json', {'albums': [], 'tracks': {}})
            overrides = read_json_at(store_fd, 'track-overrides.json', {})
            changes = 0
            for relative, entry in proposal['tracks'].items():
                try:
                    require_regular_beneath(root_fd, relative)
                except (FileNotFoundError, ValueError) as error:
                    raise ValueError(
                        'Proposal must reference existing non-symlink files beneath the library root') from error
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
                if db_exists:
                    stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
                    copy_file_exclusive_at(
                        store_fd, 'enriched.json', 'enriched.before-' + stamp + '.json')
                write_json_atomic_at(store_fd, 'enriched.json', data)
            return changes

def main():
    os.umask(0o077)
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
