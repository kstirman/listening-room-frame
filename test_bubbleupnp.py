import unittest
from bridge import Bridge, metadata_values


class BubbleMetadataTests(unittest.TestCase):
    def test_bubble_album_is_qobuz_despite_local_proxy(self):
        metadata = metadata_values('''<item id="qobuz/albums/album123/12345" parentID="0">
          <album>Example Album</album>
          <albumArtURI>http://static.qobuz.com/images/cover.jpg</albumArtURI>
          <res>http://192.0.2.130/proxy/track</res>
        </item>''')
        self.assertEqual(metadata['album_id'], 'album123')
        self.assertEqual(Bridge.album_key(metadata), 'qobuz:album123')

    def test_existing_jplay_format_preserved(self):
        metadata = metadata_values('<item id="qobuz/track/1" parentID="qobuz/album/abc123"/>')
        self.assertEqual(Bridge.album_key(metadata), 'qobuz:abc123')

    def test_unrelated_or_malformed_ids_are_not_qobuz(self):
        for identifier in ('local/album123/12345', 'qobuz/albums/../../1',
                           'qobuz/albums/abc/1/extra'):
            with self.subTest(identifier=identifier):
                self.assertNotIn('album_id', metadata_values(f'<item id="{identifier}"/>'))


if __name__ == '__main__':
    unittest.main()
