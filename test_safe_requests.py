import socket
import unittest
from unittest.mock import Mock, patch

import requests

import safe_requests


def answer(address, port=80):
    family = socket.AF_INET6 if ':' in address else socket.AF_INET
    endpoint = (address, port, 0, 0) if family == socket.AF_INET6 else (address, port)
    return [(family, socket.SOCK_STREAM, 6, '', endpoint)]


def response(status=200, content=b'', headers=None):
    result = requests.Response()
    result.status_code = status
    result.headers = headers or {}
    result._content = content
    result._content_consumed = True
    return result


class SafeRequestTests(unittest.TestCase):
    def test_untrusted_special_addresses_never_reach_transport(self):
        session = safe_requests.Session()
        for address in ('127.0.0.1', '169.254.169.254', '10.0.0.4',
                        '224.0.0.1', '240.0.0.1', '100.64.0.1', '0.0.0.0',
                        '::1', '::ffff:127.0.0.1'):
            with self.subTest(address=address), \
                    patch('safe_requests.socket.getaddrinfo', return_value=answer(address)), \
                    patch.object(session, '_send_pinned') as send:
                with self.assertRaises(ValueError):
                    session.get('http://example.test/art')
                send.assert_not_called()

    def test_numeric_loopback_alias_is_checked_after_resolution(self):
        session = safe_requests.Session()
        with patch('safe_requests.socket.getaddrinfo', return_value=answer('127.0.0.1')), \
                patch.object(session, '_send_pinned') as send:
            with self.assertRaises(ValueError):
                session.get('http://2130706433/art')
            send.assert_not_called()

    def test_approved_private_peer_is_pinned_with_original_host(self):
        session = safe_requests.Session(trusted_hosts=('music.local',))
        adapter = Mock()
        with patch('safe_requests.socket.getaddrinfo', return_value=answer('192.168.1.4', 8443)), \
                patch.object(session, '_send_pinned', return_value=(response(content=b'image'), adapter)) as send:
            result = session.get('https://music.local:8443/cover.jpg')
        prepared, hostname = send.call_args.args[:2]
        self.assertEqual(prepared.url, 'https://192.168.1.4:8443/cover.jpg')
        self.assertEqual(prepared.headers['Host'], 'music.local:8443')
        self.assertEqual(hostname, 'music.local')
        self.assertEqual(result.content, b'image')

    def test_https_adapter_preserves_sni_and_certificate_name(self):
        adapter = safe_requests._PinnedHTTPSAdapter('covers.example')
        self.assertEqual(adapter.poolmanager.connection_pool_kw['server_hostname'],
                         'covers.example')
        self.assertEqual(adapter.poolmanager.connection_pool_kw['assert_hostname'],
                         'covers.example')

    def test_redirect_to_private_peer_is_revalidated_before_send(self):
        session = safe_requests.Session()
        first = response(302, headers={'Location': 'http://127.0.0.1/admin'})
        adapter = Mock()
        def resolve(host, port, **_):
            return answer('93.184.216.34' if host == 'covers.example' else '127.0.0.1', port)
        with patch('safe_requests.socket.getaddrinfo', side_effect=resolve), \
                patch.object(session, '_send_pinned', return_value=(first, adapter)) as send:
            with self.assertRaises(ValueError):
                session.get('https://covers.example/art')
        self.assertEqual(send.call_count, 1)

    def test_approved_same_host_redirect_keeps_working(self):
        session = safe_requests.Session(trusted_hosts=('music.local',))
        redirect = response(302, headers={'Location': '/covers/current.jpg'})
        final = response(content=b'image')
        adapter = Mock()
        with patch('safe_requests.socket.getaddrinfo', return_value=answer('192.168.1.4')), \
                patch.object(session, '_send_pinned', side_effect=[(redirect, adapter),
                                                                  (final, adapter)]) as send:
            result = session.get('http://music.local/cover')
        self.assertEqual(send.call_count, 2)
        self.assertEqual(result.content, b'image')
        self.assertEqual(result.history, [redirect])

    def test_trusted_origin_does_not_approve_other_ports(self):
        session = safe_requests.Session(trusted_origins=('http://192.168.1.20:1400/',))
        adapter = Mock()
        with patch('safe_requests.socket.getaddrinfo', return_value=answer('192.168.1.20')), \
                patch.object(session, '_send_pinned', return_value=(response(), adapter)) as send:
            session.get('http://192.168.1.20:1400/cover')
            with self.assertRaises(ValueError):
                session.get('http://192.168.1.20:1401/admin')
        self.assertEqual(send.call_count, 1)

    def test_pinned_stream_keeps_response_budget_and_closes_adapter(self):
        from network_limits import fetch, ResponseLimitError
        session = safe_requests.Session()
        for content, succeeds in ((b'image', True), (b'x' * 11, False)):
            reply = response(content=content)
            adapter = Mock()
            with self.subTest(succeeds=succeeds), \
                    patch('safe_requests.socket.getaddrinfo', return_value=answer('93.184.216.34')), \
                    patch.object(session, '_send_pinned', return_value=(reply, adapter)), \
                    patch.object(session, '_consume') as consume:
                if succeeds:
                    self.assertEqual(fetch(session.get, 'https://covers.example/art',
                                           max_bytes=10, total_seconds=1).content, b'image')
                else:
                    with self.assertRaises(ResponseLimitError):
                        fetch(session.get, 'https://covers.example/art',
                              max_bytes=10, total_seconds=1)
                consume.assert_not_called()
                adapter.close.assert_called_once()



if __name__ == '__main__':
    unittest.main()
