import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from local_composition import build, Catalog


def rows():
    return {str(i): {'work': 'Example Mass', 'composer': 'Example Composer',
                    'album': f'Inconsistent album {i}', 'artist': f'Artist {i}',
                    'movementName': 'I. Chorus', 'duration_seconds': 60+i,
                    'composition_group': 'example-performance',
                    'composition_index': i, 'composition_count': 3}
            for i in range(1, 4)}


class ReviewedPartsTests(unittest.TestCase):
    def test_explicit_order_spans_tags_and_restarted_numbers(self):
        data = rows()
        data['other'] = dict(data['1'], composition_group='other-performance')
        self.assertEqual(build(data, '2'), {'index': 1, 'durations': [61, 62, 63], 'label': 'Part'})

    def test_incomplete_duplicate_and_invalid_groups_fail_closed(self):
        data = rows(); del data['3']
        self.assertIsNone(build(data, '1'))
        for change in [{'composition_index': 1}, {'composition_count': 4},
                       {'duration_seconds': None}, {'composer': 'Other'},
                       {'composition_index': 'invalid'}, {'work': 'Other'}]:
            data = rows(); data['2'].update(change)
            self.assertIsNone(build(data, '1'))

    def test_catalog_reads_reviewed_order_from_overrides(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); db = root/'.listening-room-metadata'; db.mkdir()
            data = rows(); base = {}; overrides = {}
            for key, fields in data.items():
                name = key + '.flac'; (root/name).touch()
                base[name] = {'fields': {k:v for k,v in fields.items() if not k.startswith('composition_')}}
                overrides[name] = {'fields': {k:v for k,v in fields.items() if k.startswith('composition_')}}
            (db/'enriched.json').write_text(json.dumps({'tracks': base}))
            (db/'track-overrides.json').write_text(json.dumps(overrides))
            with patch('local_composition.probe', side_effect=lambda p: {'duration_seconds': 60+int(p.stem)}):
                context = Catalog().context('http://example.test/library/2.flac', root, data['2'], '/library/')
            self.assertEqual(context, {'index': 1, 'durations': [61, 62, 63], 'label': 'Part'})
