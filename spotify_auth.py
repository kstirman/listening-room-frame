"""Authorize read-only Spotify current-track access using PKCE and a loopback callback."""
import argparse
import base64
import hashlib
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
import re
import secrets
import time
from urllib.parse import parse_qs, urlencode, urlparse
import webbrowser

import requests

from spotify_source import SCOPE, TOKEN_URL, SpotifyError, save_tokens, token_response


def authorization(client_id, redirect_uri):
    verifier = secrets.token_urlsafe(64)
    state = secrets.token_urlsafe(32)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')
    url = 'https://accounts.spotify.com/authorize?' + urlencode({
        'client_id': client_id, 'response_type': 'code', 'redirect_uri': redirect_uri,
        'scope': SCOPE, 'state': state, 'code_challenge_method': 'S256', 'code_challenge': challenge})
    return url, verifier, state


class Callback(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass  # Callback URLs contain short-lived authorization codes.

    def do_GET(self):
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        valid = (self.headers.get('Host') == self.server.expected_host
                 and parsed.path == '/callback' and not parsed.scheme
                 and len(self.path) < 8192 and len(query.get('state', [])) == 1
                 and secrets.compare_digest(query['state'][0], self.server.oauth_state))
        if not valid:
            status, message = 400, 'Invalid authorization response. Return to the original Spotify sign-in tab.'
        elif query.get('error'):
            self.server.result = {'error': 'Spotify authorization was declined.'}
            status, message = 200, 'Spotify was not connected. You can close this tab.'
        elif len(query.get('code', [])) == 1 and query['code'][0]:
            self.server.result = {'code': query['code'][0]}
            status, message = 200, 'Authorization received. Return to the terminal to confirm the connection.'
        else:
            status, message = 400, 'Spotify did not return an authorization code.'
        body = message.encode()
        self.send_response(status)
        self.send_header('Content-Type', 'text/plain; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.end_headers()
        self.wfile.write(body)


class CallbackServer(HTTPServer):
    def get_request(self):
        connection, address = super().get_request()
        connection.settimeout(5)
        return connection, address


def connect(client_id, path, port=8888, open_browser=True):
    if not re.fullmatch(r'[a-fA-F0-9]{32}', client_id):
        raise SpotifyError('Use the 32-character client ID from your Spotify app. No client secret is needed.')
    if not 1024 <= port <= 65535:
        raise SpotifyError('Choose a callback port between 1024 and 65535.')
    redirect = f'http://127.0.0.1:{port}/callback'
    url, verifier, state = authorization(client_id, redirect)
    with CallbackServer(('127.0.0.1', port), Callback) as server:
        server.timeout = 1
        server.expected_host = f'127.0.0.1:{port}'
        server.oauth_state, server.result = state, None
        print('Register this exact Redirect URI in your Spotify app:', redirect, flush=True)
        print('Open this sign-in URL in a browser on this computer:\n' + url, flush=True)
        if open_browser:
            webbrowser.open(url)
        deadline = time.monotonic() + 300
        while server.result is None and time.monotonic() < deadline:
            server.handle_request()
        if not server.result:
            raise SpotifyError('Spotify sign-in timed out. Run the command again.')
        if 'error' in server.result:
            raise SpotifyError(server.result['error'])
        response = requests.post(TOKEN_URL, data={'grant_type': 'authorization_code',
            'code': server.result['code'], 'redirect_uri': redirect,
            'client_id': client_id, 'code_verifier': verifier}, timeout=10, allow_redirects=False)
        save_tokens(path, token_response(response, client_id))
    print('Spotify connected. Private authorization saved to', Path(path).expanduser())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--client-id', required=True)
    parser.add_argument('--token-file', default='~/.local/state/listening-room-spotify/spotify-tokens.json')
    parser.add_argument('--port', type=int, default=8888)
    parser.add_argument('--no-browser', action='store_true')
    args = parser.parse_args()
    try:
        connect(args.client_id, args.token_file, args.port, not args.no_browser)
    except SpotifyError as error:
        parser.exit(1, str(error) + '\n')
    except (requests.RequestException, OSError, ValueError):
        parser.exit(1, 'Could not complete Spotify sign-in. Check connectivity and the callback port, then retry.\n')


if __name__ == '__main__':
    main()
