import copy
import unittest
import timing


ROMANS = 'I II III IV V VI VII VIII IX X XI XII XIII XIV XV XVI XVII XVIII XIX XX XXI XXII'.split()


def work():
    return {str(i): {'work': 'Unseen twenty-two-part work', 'composer': 'Fixture Composer',
                     'title': roman + '. Section', 'movementName': roman + '. Section',
                     'duration_seconds': 50 + i, '_work_complete': True}
            for i, roman in enumerate(ROMANS, 1)}


class ExtendedRomanTests(unittest.TestCase):
    def test_canonical_numbers_and_strict_rejection(self):
        for label, expected in [('XVII', 17), ('XXII', 22), ('XL', 40), ('XCIX', 99), ('CXXVIII', 128)]:
            self.assertEqual(timing.number({'title': label + '. Section'}), expected)
        for label in ['IIII', 'IIX', 'IC', 'VX', 'XXIIII', 'CXXIX', 'MMMM', 'xxii']:
            self.assertIsNone(timing.number({'title': label + '. Section'}))

    def test_complete_unseen_22_part_work_at_every_position(self):
        tracks = work()
        for i in range(1, 23):
            self.assertEqual(timing.composition(tracks, str(i)),
                             {'index': i - 1, 'durations': list(range(51, 73))})

    def test_partial_missing_middle_duplicate_and_reset(self):
        for kind in ['partial', 'missing', 'duplicate', 'reset', 'duration']:
            tracks = work()
            if kind == 'partial':
                tracks['22']['_work_complete'] = False
            elif kind == 'missing':
                del tracks['10']
            elif kind == 'duplicate':
                tracks['10']['movementName'] = 'IX. Duplicate'
            elif kind == 'reset':
                tracks['10']['movementName'] = 'I. Restart'
            else:
                del tracks['10']['duration_seconds']
            for key in tracks:
                self.assertIsNone(timing.composition(tracks, key), kind)

    def test_changed_work_composer_or_scope_cannot_make_22_part_group(self):
        for field in ['work', 'composer', 'work_scope']:
            tracks = work()
            tracks['12'][field] = 'Unrelated'
            for key in tracks:
                result = timing.composition(tracks, key)
                self.assertTrue(result is None or len(result['durations']) < 22)
            self.assertIsNone(timing.composition(tracks, '22'))

    def test_explicit_reviewed_22_part_group_still_works(self):
        tracks = work()
        for i, row in enumerate(tracks.values(), 1):
            row.update(composition_group='source-group', composition_count=22,
                       composition_index=i, _edition_verified='edition', _catalog_album_id='edition')
        self.assertEqual(timing.composition(tracks, '22')['index'], 21)
        tracks['9']['composer'] = 'Other'
        self.assertIsNone(timing.composition(tracks, '22'))
