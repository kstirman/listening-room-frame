import tempfile
import unittest
from unittest.mock import Mock, patch
from xml.sax.saxutils import escape

import requests

from bridge import Bridge
from network_limits import (ResponseDeadlineError, ResponseLimitError, fetch,
                            json_value, xml_root)


class FakeResponse:
    def __init__(self, chunks, headers=None, status=200):
        self.chunks = chunks
        self.headers = headers or {}
        self.status_code = status
        self.encoding = 'utf-8'
        self.closed = False

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))

    def iter_content(self, chunk_size):
        return iter(self.chunks)

    def close(self):
        self.closed = True


def mock_response(content, headers=None, status=200):
    return Mock(content=content, headers=headers or {}, status_code=status,
                encoding='utf-8')


class NetworkLimitTests(unittest.TestCase):
    def test_bounded_fetch_preserves_small_json(self):
        response = FakeResponse([b'{"device":', b'{"modelName":"FRAME"}}'])
        request = Mock(return_value=response)
        result = fetch(request, 'http://frame/api', max_bytes=100,
                       total_seconds=4, timeout=4)
        self.assertEqual(result.json(max_bytes=100)['device']['modelName'], 'FRAME')
        request.assert_called_once_with(
            'http://frame/api', timeout=4, headers={'Accept-Encoding': 'identity'},
            stream=True, allow_redirects=False)
        self.assertTrue(response.closed)

    def test_declared_and_streamed_excess_are_rejected(self):
        declared = FakeResponse([], {'Content-Length': '6'})
        with self.assertRaises(ResponseLimitError):
            fetch(Mock(return_value=declared), 'http://peer', max_bytes=5,
                  total_seconds=1, timeout=1)
        self.assertTrue(declared.closed)
        streamed = FakeResponse([b'abcd', b'ef'])
        with self.assertRaises(ResponseLimitError):
            fetch(Mock(return_value=streamed), 'http://peer', max_bytes=5,
                  total_seconds=1, timeout=1)
        self.assertTrue(streamed.closed)
        encoded = FakeResponse([b'compressed'], {'Content-Encoding': 'gzip'})
        with self.assertRaises(ResponseLimitError):
            fetch(Mock(return_value=encoded), 'http://peer', max_bytes=20,
                  total_seconds=1, timeout=1)
        self.assertTrue(encoded.closed)

    def test_total_deadline_closes_response(self):
        response = FakeResponse([b'ok'])
        with patch('network_limits.time.monotonic', side_effect=[0, 0, 2]):
            with self.assertRaises(ResponseDeadlineError):
                fetch(Mock(return_value=response), 'http://peer', max_bytes=5,
                      total_seconds=1, timeout=1)
        self.assertTrue(response.closed)

    def test_same_origin_redirect_remains_bounded(self):
        redirect = FakeResponse([], {'Location': '/final'}, status=302)
        final = FakeResponse([b'ok'])
        result = fetch(Mock(side_effect=[redirect, final]),
                       'https://catalog.example/start', max_bytes=2,
                       total_seconds=2, timeout=2, same_origin_redirects=1)
        self.assertEqual(result.content, b'ok')
        self.assertTrue(redirect.closed and final.closed)
        foreign = FakeResponse([], {'Location': 'https://other.example/final'},
                               status=302)
        with self.assertRaises(requests.TooManyRedirects):
            fetch(Mock(return_value=foreign), 'https://catalog.example/start',
                  max_bytes=2, total_seconds=2, timeout=2,
                  same_origin_redirects=1)

    def test_parser_structure_budgets(self):
        self.assertEqual(json_value('{"ok":[1]}', max_bytes=20), {'ok': [1]})
        with self.assertRaises(ResponseLimitError):
            json_value('[[[0]]]', max_bytes=20, max_depth=2)
        self.assertEqual(xml_root('<root><item>ok</item></root>',
                                  max_bytes=100).find('item').text, 'ok')
        with self.assertRaises(ResponseLimitError):
            xml_root('<a><b><c/></b></a>', max_bytes=100, max_depth=2)
        with self.assertRaises(ResponseLimitError):
            xml_root('<!DOCTYPE a><a/>', max_bytes=100)


class BrowseLimitTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.bridge = Bridge({'local_art_hosts': [],
                              'minim_control': 'http://library/control'},
                             self.temp.name)
        self.bridge.session = Mock()

    @staticmethod
    def browse_response(result, returned, total):
        body = ('<Envelope><Body><BrowseResponse><Result>' + escape(result) +
                '</Result><NumberReturned>' + str(returned) +
                '</NumberReturned><TotalMatches>' + str(total) +
                '</TotalMatches></BrowseResponse></Body></Envelope>')
        return mock_response(body.encode())

    def test_single_browse_page_preserves_lookup(self):
        uri = 'http://library/music/track.flac'
        result = ('<DIDL-Lite><item id="local/1" parentID="local/album">'
                  '<title>Track</title><res>' + uri + '</res>'
                  '</item></DIDL-Lite>')
        self.bridge.session.post.return_value = self.browse_response(result, 1, 1)
        self.assertEqual(self.bridge.lookup_local(uri)['title'], 'Track')
        call = self.bridge.session.post.call_args
        self.assertTrue(call.kwargs['stream'])
        self.assertFalse(call.kwargs['allow_redirects'])
        self.assertEqual(call.kwargs['headers']['Accept-Encoding'], 'identity')

    def test_untrusted_browse_counters_fail_closed(self):
        empty = '<DIDL-Lite></DIDL-Lite>'
        for returned, total in ((0, 1), (0, 10_001), (-1, 1)):
            with self.subTest(returned=returned, total=total):
                self.bridge.index_at = 0
                self.bridge.session.post.return_value = self.browse_response(
                    empty, returned, total)
                with self.assertRaises(ValueError):
                    self.bridge.lookup_local('http://library/missing.flac')


if __name__ == '__main__':
    unittest.main()
