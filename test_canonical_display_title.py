import copy
import unittest
from unittest.mock import Mock

import test_cached_ordinal


class CanonicalDisplayTitleTests(unittest.TestCase):
    def setUp(self):
        test_cached_ordinal.CachedOrdinalTests.setUp(self)
        self.rows['1'].update(
            title='Damaged Name', artist='Performer', duration_seconds=243,
            _canonical_title='Correct Nāme', compositionDate='1900',
            recordingDate='2000')
        self.m = dict(album_id='abc', artist='Performer', title='Damaged Name',
                      duration_seconds=243)
        self.b.independent_catalog.lookup = Mock(return_value={})

    def test_reviewed_correction_after_matching(self):
        before = copy.deepcopy(self.rows)
        details = self.b.details(self.m)
        self.assertEqual(details['display_track'], 'Correct Nāme')
        self.assertEqual(details['recording_year'], '2000')
        self.assertEqual(details['composition_year'], '1900')
        self.assertEqual(self.rows, before)
        self.assertEqual(self.m['title'], 'Damaged Name')
        self.b.album_page.assert_not_called()

    def test_no_matching_alias_created(self):
        details = self.b.details(dict(self.m, title='Correct Nāme'))
        self.assertNotEqual(details.get('catalog_status'), 'catalog_id')
        self.assertNotIn('recording_year', details)

    def test_metadata_cannot_supply_correction(self):
        del self.rows['1']['_canonical_title']
        details = self.b.details(dict(self.m, _canonical_title='Injected'))
        self.assertEqual(details['display_track'], 'Damaged Name')

    def test_invalid_corrections_ignored(self):
        for value in [None, '', '  ', [], {}, 42]:
            with self.subTest(value=value):
                self.b.detail_cache.clear()
                self.rows['1']['_canonical_title'] = value
                self.assertEqual(self.b.details(self.m)['display_track'], 'Damaged Name')

    def test_exact_review_identity_required(self):
        for field in ['_edition_verified', '_catalog_album_id']:
            for value in [None, 'other']:
                with self.subTest(field=field, value=value):
                    self.b.detail_cache.clear()
                    before = copy.deepcopy(self.rows['1'])
                    self.rows['1'][field] = value
                    # Direct lookup isolates the display guard from match rejection.
                    metadata = dict(self.m, object_id='qobuz/track/1')
                    self.assertEqual(self.b.details(metadata)['display_track'], 'Damaged Name')
                    self.rows['1'] = before

    def test_nonselected_row_cannot_supply_title(self):
        del self.rows['1']['_canonical_title']
        self.rows['2']['_canonical_title'] = 'Other correction'
        self.assertEqual(self.b.details(self.m)['display_track'], 'Damaged Name')

    def test_plain_title_behavior_preserved(self):
        del self.rows['1']['_canonical_title']
        self.assertEqual(self.b.details(self.m)['display_track'], 'Damaged Name')

    def test_whitespace_trimmed_for_display_only(self):
        self.rows['1']['_canonical_title'] = '  Correct Nāme  '
        self.assertEqual(self.b.details(self.m)['display_track'], 'Correct Nāme')
        self.assertEqual(self.rows['1']['_canonical_title'], '  Correct Nāme  ')
