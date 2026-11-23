import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import library_enrich


class LibraryEnrichSecurityTests(unittest.TestCase):
    def scan(self, root):
        with mock.patch.multiple(
                library_enrich,
                ROOT=root,
                STORE=root / '.listening-room-metadata',
                CACHE=root / '.listening-room-metadata/http-cache',
                ART=root / '.listening-room-metadata/covers'):
            return library_enrich.scan()

    def media(self, *, picture=None):
        result = mock.Mock()
        result.tags = {'title': ['Title'], 'album': ['Album'], 'artist': ['Artist']}
        result.pictures = [] if picture is None else [mock.Mock(data=picture)]
        result.info.length = 12.5
        return result

    def test_regular_media_inventory_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'track.flac').write_bytes(b'audio')
            with mock.patch.object(library_enrich.mutagen, 'File', return_value=self.media()):
                result = self.scan(root)
            self.assertEqual(result['albums'][0]['tracks'][0]['path'], 'track.flac')
            self.assertEqual(result['albums'][0]['tracks'][0]['duration'], 12.5)

    def test_media_symlink_is_rejected_before_parser(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'library'
            root.mkdir()
            outside = Path(directory) / 'outside.flac'
            outside.write_bytes(b'outside')
            (root / 'linked.flac').symlink_to(outside)
            with mock.patch.object(library_enrich.mutagen, 'File') as parser:
                result = self.scan(root)
            parser.assert_not_called()
            self.assertEqual(result['albums'], [])
            self.assertEqual(len(result['errors']), 1)

    def test_metadata_directory_symlink_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'library'
            outside = Path(directory) / 'outside'
            root.mkdir(); outside.mkdir()
            (root / '.listening-room-metadata').symlink_to(outside, target_is_directory=True)
            with self.assertRaises(ValueError):
                self.scan(root)
            self.assertEqual(list(outside.iterdir()), [])

    def test_inventory_symlink_does_not_change_external_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'library'
            root.mkdir()
            store = root / '.listening-room-metadata'
            store.mkdir(); (store / 'http-cache').mkdir(); (store / 'covers').mkdir()
            outside = Path(directory) / 'outside.json'
            outside.write_text('unchanged')
            (store / 'inventory.json').symlink_to(outside)
            with self.assertRaises(ValueError):
                self.scan(root)
            self.assertEqual(outside.read_text(), 'unchanged')

    def test_cover_symlink_does_not_change_external_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'library'
            root.mkdir(); (root / 'track.flac').write_bytes(b'audio')
            store = root / '.listening-room-metadata'
            store.mkdir(); (store / 'http-cache').mkdir(); covers = store / 'covers'; covers.mkdir()
            outside = Path(directory) / 'outside.jpg'
            outside.write_bytes(b'unchanged')
            cover_name = hashlib.sha256('.|Album'.encode()).hexdigest() + '.jpg'
            (covers / cover_name).symlink_to(outside)
            with mock.patch.object(library_enrich.mutagen, 'File', return_value=self.media(picture=b'image')):
                result = self.scan(root)
            self.assertEqual(outside.read_bytes(), b'unchanged')
            self.assertEqual(len(result['errors']), 1)

    def test_linked_folder_cover_is_not_recorded(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'library'
            root.mkdir(); (root / 'track.flac').write_bytes(b'audio')
            outside = Path(directory) / 'outside.jpg'
            outside.write_bytes(b'outside')
            (root / 'cover.jpg').symlink_to(outside)
            with mock.patch.object(library_enrich.mutagen, 'File', return_value=self.media()):
                result = self.scan(root)
            self.assertNotIn('cover', result['albums'][0])


if __name__ == '__main__':
    unittest.main()
