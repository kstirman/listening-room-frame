"""Explicit, bounded import from a Google Photos shared page into a private cache.

Unofficial shared-page format; this imports the initial page and reports when
pagination remains. No cookies, account API, periodic sync, or TV operations.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta
import hashlib
import io
import json
import os
from pathlib import Path
import re
from urllib.parse import urlparse, urljoin
import requests
from PIL import Image, ImageOps


def get(url, limit, hosts):
    for _ in range(5):
        parsed = urlparse(url)
        if parsed.scheme != 'https' or parsed.hostname not in hosts or parsed.username or parsed.password:
            raise ValueError('Unapproved photo source host')
        with requests.get(url, stream=True, allow_redirects=False, timeout=(5, 25)) as r:
            if r.is_redirect:
                url = urljoin(url, r.headers['Location']); continue
            r.raise_for_status()
            chunks = []; size = 0
            for chunk in r.iter_content(65536):
                size += len(chunk)
                if size > limit: raise ValueError('Photo source exceeds size limit')
                chunks.append(chunk)
            return b''.join(chunks)
    raise ValueError('Too many source redirects')


def page_data(html):
    match = re.search(r"key:\s*'ds:1'\s*,\s*hash:\s*'[^']*'\s*,\s*data:", html)
    if not match: raise ValueError('Shared page format not recognized')
    data, _ = json.JSONDecoder().raw_decode(html[match.end():])
    if not isinstance(data, list) or len(data) < 3 or not isinstance(data[1], list):
        raise ValueError('Shared album data not recognized')
    return data


def date_taken(row):
    # Milliseconds plus per-photo UTC offset, checked against Photos Date taken.
    try:
        stamp, offset = row[2], row[4]
        if (type(stamp) is not int or type(offset) is not int or
                abs(offset) > 14 * 3600 * 1000):
            return ''
        day = datetime.fromtimestamp(stamp/1000, timezone(timedelta(milliseconds=offset)))
        return day.date().isoformat()
    except (ValueError, TypeError, IndexError, OverflowError, OSError):
        return ''


def import_album(data, destination, fetch=get):
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True, mode=0o700)
    (destination/'images').mkdir(exist_ok=True, mode=0o700)
    manifest = destination/'photos.json'
    previous = {}
    if manifest.exists():
        previous = {p['file']:p for p in json.loads(manifest.read_text()).get('photos', [])}
    photos = []; failed = 0; videos = 0; seen = set()
    def one(row):
        try:
            if not isinstance(row, list) or len(row) < 10 or not isinstance(row[9], dict):
                raise ValueError('Unknown media row')
            if '76647426' in row[9]:
                return None, 0, 1
            identity, media = row[0], row[1]
            if not isinstance(identity, str) or not isinstance(media, list): raise ValueError('Unknown media')
            filename = 'images/' + hashlib.sha256(identity.encode()).hexdigest() + '.jpg'
            target = destination/filename
            if not target.exists():
                blob = fetch(media[0]+'=w3840-h2160', 30_000_000, {'lh3.googleusercontent.com'})
                with Image.open(io.BytesIO(blob)) as image:
                    image = ImageOps.exif_transpose(image).convert('RGB')
                    image.thumbnail((3840, 2160), Image.Resampling.LANCZOS)
                    temp = target.with_suffix('.tmp')
                    image.save(temp, format='JPEG', quality=95)
                    temp.replace(target)
            item = dict(previous.get(filename, {}), file=filename)
            day = date_taken(row)
            if day: item.update(date=day, date_source='Google Photos Date taken (shared album)')
            return item, 0, 0
        except (OSError, ValueError, TypeError, IndexError, requests.RequestException):
            return None, 1, 0
    unique = []
    for row in data[1][:300]:
        identity = row[0] if isinstance(row, list) and row and isinstance(row[0], str) else None
        if identity and identity in seen: continue
        if identity: seen.add(identity)
        unique.append(row)
    with ThreadPoolExecutor(max_workers=4) as pool:
        for item, failure, video in pool.map(one, unique):
            failed += failure; videos += video
            if item: photos.append(item)
    if not photos: raise ValueError('No photos imported; previous playlist retained')
    result = {'schema':1, 'source':'Google Photos shared album', 'coverage':'initial page',
              'more_available':bool(data[2]), 'failed':failed, 'videos_skipped':videos, 'photos':photos}
    temp = manifest.with_suffix('.tmp'); temp.write_text(json.dumps(result, indent=2)); temp.replace(manifest)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--album-url-file', type=Path, help='Private text file holding the shared URL')
    source.add_argument('--saved-page', type=Path, help='Previously saved shared page HTML')
    parser.add_argument('--destination', required=True, type=Path)
    args = parser.parse_args(); os.umask(0o077)
    try:
        html = (args.saved_page.read_text() if args.saved_page else
                get(args.album_url_file.read_text().strip(), 5_000_000,
                    {'photos.app.goo.gl','photos.google.com'}).decode('utf-8'))
        result = import_album(page_data(html), args.destination)
        print(json.dumps({k:v for k,v in result.items() if k != 'photos'} | {'imported':len(result['photos'])}))
    except Exception as error:
        # Exceptions may embed private shared URLs; never print their text.
        raise SystemExit('Photo import failed: '+type(error).__name__)


if __name__ == '__main__':
    main()
