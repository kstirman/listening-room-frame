"""Read-only UPnP listener; independent Samsung Frame Art Mode display."""
import argparse
import hashlib
import io
import json
import logging
import os
from pathlib import Path
import re
import signal
import time
from html.parser import HTMLParser
from urllib.parse import urlparse
import xml.etree.ElementTree as ET
import layouts
import library_metadata
import classical
import dates

import requests
from PIL import Image, ImageOps
from samsungtvws.art.art import SamsungTVArt

LOG = logging.getLogger('frame-art')
SERVICE = 'urn:schemas-upnp-org:service:AVTransport:1'


class MetaParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.meta = {}
        self.structured = []
        self.in_json = False
        self.json_text = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'script' and attrs.get('type') == 'application/ld+json':
            self.in_json = True
            self.json_text = []
        if tag == 'meta' and attrs.get('property') in ('og:image', 'og:title'):
            self.meta[attrs['property']] = attrs.get('content', '')

    def handle_data(self, data):
        if self.in_json:
            self.json_text.append(data)

    def handle_endtag(self, tag):
        if tag == 'script' and self.in_json:
            self.in_json = False
            try:
                self.structured.append(json.loads(''.join(self.json_text)))
            except ValueError:
                pass


def metadata_values(xml):
    root = ET.fromstring(xml)
    result = {}
    for item in root.iter():
        if item.tag.split('}')[-1] == 'item':
            result['object_id'] = item.get('id', '')
            match = re.fullmatch(r'qobuz/album/([a-zA-Z0-9]+)', item.get('parentID', ''))
            if match:
                result['album_id'] = match[1]
        name = item.tag.split('}')[-1]
        if name in ('albumArtURI', 'album', 'title', 'genre', 'date') and item.text:
            result[name] = item.text
        if name in ('artist', 'author') and item.text:
            role = item.get('role', '').lower()
            key = {'albumartist': 'albumArtist', 'composer': 'composer',
                   'conductor': 'conductor', 'orchestra': 'orchestra',
                   'performer': 'performer', 'soloist': 'soloist'}.get(role)
            if not role:
                key = 'artist'
            if key:
                existing = result.get(key, '').split('; ') if result.get(key) else []
                if item.text not in existing:
                    result[key] = '; '.join(existing + [item.text])
        if name == 'userAnnotation' and item.text and item.text.startswith('tag.'):
            field, separator, value = item.text[4:].partition('=')
            key = {'work': 'work', 'group': 'group', 'movement': 'movement',
                   'movementname': 'movementName', 'movementnumber': 'movementNumber'}.get(field.lower())
            if key and separator:
                result[key] = value
        if name == 'res' and item.text:
            result['resource'] = item.text
    return result


class Bridge:
    def __init__(self, config, root):
        self.config = config
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.state_file = self.root / 'state.json'
        self.state = json.loads(self.state_file.read_text()) if self.state_file.exists() else {}
        self.session = requests.Session()
        self.session.headers['User-Agent'] = 'ListeningRoomArtwork/1.0'
        self.stopped_at = None
        self.blocked_album = None
        self.running = True
        self.pages = {}
        self.detail_cache = {}
        self.local_index = {}
        self.index_at = 0
        self.last_tv_check = 0
        self.next_collage = time.monotonic() + self.config.get('collage_interval_seconds', 120)
        self.collage_until = 0 if self.state.get('view') == 'collage' else None
        self.state.setdefault('history', [])
        (self.root / 'covers').mkdir(exist_ok=True)
        self.normalize_history()


    def save(self):
        temp = self.state_file.with_suffix('.tmp')
        temp.write_text(json.dumps(self.state, indent=2))
        temp.replace(self.state_file)

    def soap(self, action):
        body = f'<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/"><s:Body><u:{action} xmlns:u="{SERVICE}"><InstanceID>0</InstanceID></u:{action}></s:Body></s:Envelope>'
        response = self.session.post(self.config.get('renderer_control_url') or self.config['renderer'] + '/AVTransport/control', data=body,
                                     headers={'SOAPAction': f'"{SERVICE}#{action}"', 'Content-Type': 'text/xml'}, timeout=8)
        response.raise_for_status()
        return {el.tag.split('}')[-1]: el.text for el in ET.fromstring(response.content).iter()}

    def playback(self):
        status = self.soap('GetTransportInfo').get('CurrentTransportState')
        if status != 'PLAYING':
            return status, {}
        position = self.soap('GetPositionInfo')
        metadata = position.get('TrackMetaData')
        result = metadata_values(metadata) if metadata and metadata != 'NOT_IMPLEMENTED' else {}
        uri = position.get('TrackURI') or result.get('resource', '')
        if urlparse(uri).hostname in self.config['local_art_hosts']:
            result['resource'] = uri
            if not result.get('albumArtURI'):
                result.update(self.lookup_local(uri))
        return status, result

    def lookup_local(self, uri):
        # Fallback for controllers that omit artwork from renderer metadata.
        # The index contains only MinimServer's local library, never Qobuz URLs.
        if time.monotonic() - self.index_at > 3600 or not self.index_at:
            index = {}
            start = 0
            service = 'urn:schemas-upnp-org:service:ContentDirectory:1'
            while True:
                body = f'<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/"><s:Body><u:Browse xmlns:u="{service}"><ObjectID>0$items</ObjectID><BrowseFlag>BrowseDirectChildren</BrowseFlag><Filter>*</Filter><StartingIndex>{start}</StartingIndex><RequestedCount>500</RequestedCount><SortCriteria></SortCriteria></u:Browse></s:Body></s:Envelope>'
                r = self.session.post(self.config['minim_control'], data=body,
                                      headers={'SOAPAction': f'"{service}#Browse"', 'Content-Type': 'text/xml'}, timeout=15)
                r.raise_for_status()
                response = {el.tag.split('}')[-1]: el.text for el in ET.fromstring(r.content).iter()}
                for item in ET.fromstring(response['Result']):
                    values = metadata_values(ET.tostring(item, encoding='unicode'))
                    resource = values.get('resource')
                    if resource:
                        index[resource] = values
                count = int(response['NumberReturned'])
                start += count
                if not count or start >= int(response['TotalMatches']):
                    break
            self.local_index = index
            self.index_at = time.monotonic()
        return self.local_index.get(uri, {})

    @staticmethod
    def album_key(metadata):
        if metadata.get('album_id'):
            return 'qobuz:' + metadata['album_id']
        if metadata.get('albumArtURI'):
            if metadata.get('album') and metadata.get('resource'):
                directory = urlparse(metadata['resource']).path.rsplit('/', 1)[0]
                return Bridge.canonical_album_key('local:' + directory + ':' + metadata['album'])
            return metadata['albumArtURI']
        return None

    @staticmethod
    def canonical_album_key(key):
        if not isinstance(key, str) or not key.startswith('local:'):
            return key
        # Merge numbered disc folders only when the album title is identical.
        # Keep the parent path, so unrelated editions remain separate albums.
        key = re.sub(r'/(?:CD|Disc|Disk)(?:\*20|%20|[ _-])*[0-9]+(?=:)', '', key, flags=re.I)
        # These two merged sets use legacy folder names rather than CD numbers.
        key = re.sub(r'/Platz\.HS\.LvB[1-6](?=:Beethoven - Symphonies 1-9$)', '', key)
        return re.sub(r'(/The_Cobra_Records_Story)_Disc_[0-9]+(?=:The Cobra Records Story$)', r'\1', key)

    def normalize_history(self):
        seen = set()
        history = []
        changed = False
        for original in self.state['history']:
            entry = dict(original)
            entry['key'] = self.canonical_album_key(entry['key'])
            if entry['key'] in seen:
                changed = True
                continue
            seen.add(entry['key'])
            changed |= entry != original
            history.append(entry)
        self.state['history'] = history
        old = self.state.get('album')
        new = self.canonical_album_key(old)
        if old != new:
            self.state['album'] = new
            display = self.state.get('display_key', '')
            if display.startswith(old + ':'):
                self.state['display_key'] = new + display[len(old):]
            changed = True
        if changed:
            self.state.pop('collage_revision', None)
            self.save()

    def television(self):
        # Do not send wake/power/input/audio commands. Verify TV identity first.
        r = self.session.get(f"http://{self.config['tv']}:8001/api/v2/", timeout=4)
        r.raise_for_status()
        device = r.json()['device']
        if device.get('modelName') != self.config['tv_model']:
            raise RuntimeError('TV identity differs from configured model')
        # Samsung may report standby while Art Mode is displaying artwork.
        # Query Art Mode directly; never issue a wake, power or input command.
        tv = SamsungTVArt(self.config['tv'], port=8002, timeout=12, key_press_delay=0.1,
                          name='Listening Room Frame', token_file=str(self.root / 'token'))
        try:
            if tv.get_artmode() != 'on':
                tv.close()
                return None
            return tv
        except Exception:
            tv.close()
            raise

    def album_page(self, album_id):
        if album_id not in self.pages:
            r = self.session.get('https://www.qobuz.com/us-en/album/-/' + album_id, timeout=15)
            r.raise_for_status()
            if len(self.pages) >= 8:
                self.pages.pop(next(iter(self.pages)))
            self.pages[album_id] = r.text
        return self.pages[album_id]

    def details(self, metadata):
        track = metadata.get('object_id') or metadata.get('resource') or json.dumps(metadata, sort_keys=True)
        if track in self.detail_cache:
            return self.detail_cache[track]
        enriched = dict(metadata)
        if metadata.get('album_id'):
            parser = classical.Tracks()
            page = self.album_page(metadata['album_id'])
            parser.feed(page)
            album_parser = MetaParser()
            album_parser.feed(page)
            for record in album_parser.structured:
                if isinstance(record, dict) and record.get('@type') in ('Product', 'MusicAlbum'):
                    value = record.get('releaseDate') or record.get('datePublished')
                    if value:enriched.setdefault('releaseDate', value)
            enriched.update(parser.tracks.get(track.rsplit('/', 1)[-1], {}))
        elif metadata.get('resource') and self.config.get('library_root'):
            try:
                enriched.update(classical.file_tags(metadata['resource'], self.config['library_root'], self.config.get('library_resource_prefix', '/minimserver/*/AudirvanaLibrary/')))
            except Exception as error:
                LOG.warning('File metadata unavailable: %s', type(error).__name__)
            # Reviewed metadata remains usable even if ffprobe cannot read a file.
            try:
                enriched.update(library_metadata.lookup(metadata['resource'], self.config['library_root'], self.config.get('library_resource_prefix', '/minimserver/*/AudirvanaLibrary/')))
            except Exception as error:
                LOG.warning('Saved metadata unavailable: %s', type(error).__name__)
        details = classical.normalize(enriched)
        date_fields = dates.extract(enriched, details)
        if date_fields:
            details = dict(details or {}, **date_fields)
        # Preserve reviewed local captions even when renderer/file tags are blank.
        if not metadata.get('album_id'):
            captions = {'display_album': enriched.get('album'),
                        'display_artist': enriched.get('albumArtist') or enriched.get('artist')}
            captions = {k: v for k, v in captions.items() if v}
            if captions:
                details = dict(details or {}, **captions)
        if len(self.detail_cache) >= 128:
            self.detail_cache.pop(next(iter(self.detail_cache)))
        self.detail_cache[track] = details
        return details

    def card(self, metadata, details):
        album = self.album_key(metadata)
        entry = next((e for e in self.state['history'] if e['key'] == album), None)
        if album != self.state.get('album') or entry is None:
            image, title = self.cover(metadata)
            entry = next((e for e in self.state['history'] if e['key'] == album), None)
        else:
            image, title = self.root / 'current-cover.jpg', entry['title']
        if details and details.get('work') and entry:
            layouts.classical(self.root / entry['cover'], details, image)
        elif entry:
            title = (details or {}).get('display_album') or title
            artist = (details or {}).get('display_artist') or entry.get('artist', '')
            layouts.current(self.root / entry['cover'], title, artist, image, date_line=dates.display_line(details))
        return image, title

    def cover(self, metadata):
        album_id = metadata.get('album_id')
        title = metadata.get('album', '')
        artist = metadata.get('albumArtist') or metadata.get('artist', '')
        if album_id:
            parser = MetaParser()
            parser.feed(self.album_page(album_id))
            url = parser.meta.get('og:image', '')
            title = parser.meta.get('og:title', '').removesuffix(' - Qobuz')
            for record in parser.structured:
                if isinstance(record, dict) and record.get('@type') == 'Product':
                    title = record.get('name') or title
                    brand = record.get('brand', {})
                    if isinstance(brand, dict):
                        artist = brand.get('name') or artist
            if urlparse(url).hostname != 'static.qobuz.com':
                raise ValueError('Qobuz cover URL missing or unexpected')
        else:
            overrides = self.config.get('local_art_overrides', {})
            directory = urlparse(metadata.get('resource', '')).path.rsplit('/', 1)[0]
            legacy = 'local:' + directory + ':' + metadata.get('album', '')
            url = overrides.get(legacy, overrides.get(
                self.album_key(metadata), metadata.get('albumArtURI', '')))
            parsed = urlparse(url)
            # Known home music hosts only; never use the signed audio resource URL.
            if parsed.scheme not in ('http', 'https') or parsed.hostname not in self.config['local_art_hosts']:
                raise ValueError('No supported local cover URL')
        r = self.session.get(url, timeout=15)
        r.raise_for_status()
        if len(r.content) > 20_000_000:
            raise ValueError('Cover exceeds size limit')
        cover = ImageOps.exif_transpose(Image.open(io.BytesIO(r.content))).convert('RGB')
        key = self.album_key(metadata)
        filename = 'covers/' + hashlib.sha256(key.encode()).hexdigest() + '.jpg'
        cover.save(self.root / filename, quality=95)
        history = [entry for entry in self.state['history'] if entry['key'] != key]
        history.insert(0, {'key': key, 'cover': filename, 'title': title, 'artist': artist})
        removed = history[self.config.get('history_limit', 28):]
        self.state['history'] = history[:self.config.get('history_limit', 28)]
        self.state['artist'] = artist
        self.save()
        for entry in removed:
            (self.root / entry['cover']).unlink(missing_ok=True)
        output = self.root / 'current-cover.jpg'
        layouts.current(self.root / filename, title, artist, output)
        return output, title

    def owned_ids(self):
        return {self.state.get(name) for name in ('owned', 'album_image', 'collage_image')} - {None}

    def select(self, tv, content):
        # Save pending selection before the write, so restart/restore can recover
        # a TV change even if the response is lost.
        self.state['pending'] = content
        self.save()
        tv.select_image(content)
        if tv.get_current()['content_id'] != content:
            raise RuntimeError('Artwork selection not confirmed')
        self.state['owned'] = content
        self.state.pop('pending', None)
        self.save()

    def rotate(self, tv, now):
        if self.state.get('view') == 'collage':
            if self.collage_until is None or now >= self.collage_until:
                self.select(tv, self.state['album_image'])
                self.state['view'] = 'album'
                self.collage_until = None
                self.save()
                LOG.info('Returned to current album')
            return
        if now < self.next_collage or not self.state.get('history'):
            return
        revision = '|'.join(entry['key'] for entry in self.state['history'])
        if self.state.get('collage_revision') != revision or not self.state.get('collage_image'):
            output = self.root / 'collage.jpg'
            layouts.collage(self.state['history'], self.root, output)
            content = tv.upload(str(output), matte='none')
            old = self.state.get('collage_image')
            if old:
                self.state.setdefault('garbage', []).append(old)
            self.state.update(collage_image=content, collage_revision=revision)
            self.save()
        self.select(tv, self.state['collage_image'])
        self.state['view'] = 'collage'
        selected_at = time.monotonic()
        self.collage_until = selected_at + self.config.get('collage_duration_seconds', 15)
        self.next_collage = selected_at + self.config.get('collage_interval_seconds', 120)
        self.save()
        self.cleanup(tv)
        LOG.info('Collage displayed: %d recent albums for %ds', len(self.state['history']), self.config.get('collage_duration_seconds', 15))

    def cleanup(self, tv):
        remaining = []
        for content in set(self.state.get('garbage', [])):
            if content in self.owned_ids() or content == self.state.get('original'):
                remaining.append(content)
                continue
            try:
                if not tv.delete(content):
                    remaining.append(content)
            except Exception:
                remaining.append(content)
        self.state['garbage'] = remaining
        self.save()

    def restore(self):
        owned = self.owned_ids()
        if self.state.get('pending'):
            owned.add(self.state['pending'])
        if not owned:
            return
        tv = self.television()
        if tv is None:
            return
        try:
            if tv.get_artmode() != 'on':
                return
            current = tv.get_current()['content_id']
            if current in owned:
                tv.select_image(self.state['original'])
                if tv.get_current()['content_id'] != self.state['original']:
                    raise RuntimeError('Restore not confirmed')
                LOG.info('Original artwork restored')
            self.state.setdefault('garbage', []).extend(owned)
            for field in ('owned', 'album', 'album_image', 'collage_image', 'collage_revision', 'view', 'pending'):
                self.state.pop(field, None)
            self.collage_until = None
            self.next_collage = time.monotonic() + self.config.get('collage_interval_seconds', 120)
            self.save()
            self.cleanup(tv)
        finally:
            tv.close()

    def step(self):
        status, metadata = self.playback()
        if status != 'PLAYING':
            self.blocked_album = None
            if self.stopped_at is None:
                self.stopped_at = time.monotonic()
            if self.state.get('view') == 'collage':
                tv = self.television()
                if tv is not None:
                    try:
                        if tv.get_artmode() == 'on' and tv.get_current()['content_id'] in self.owned_ids():
                            self.collage_until = 0
                            self.rotate(tv, time.monotonic())
                    finally:
                        tv.close()
            if time.monotonic() - self.stopped_at >= self.config['idle_seconds']:
                self.restore()
            return
        self.stopped_at = None
        album = self.album_key(metadata)
        if not album:
            self.restore()
            return
        if album == self.blocked_album:
            return
        details = self.details(metadata)
        display_key = album + ':' + json.dumps(details, sort_keys=True, ensure_ascii=False)
        display_changed = self.state.get('display_key', album + ':null') != display_key
        now = time.monotonic()
        due = (now >= self.collage_until if self.collage_until is not None else now >= self.next_collage)
        if (album == self.state.get('album') and self.state.get('owned')
                and now - self.last_tv_check < 30 and not due and not display_changed):
            return
        started = time.monotonic()
        tv = self.television()
        if tv is None:
            return
        try:
            if tv.get_artmode() != 'on':
                return
            current = tv.get_current()['content_id']
            self.last_tv_check = time.monotonic()
            owned = self.state.get('owned')
            if owned and current not in self.owned_ids() | {self.state.get('pending')}:
                # Respect a manual art change until a new album or playback session.
                self.blocked_album = album
                self.restore()
                return
            if album == self.state.get('album') and current in self.owned_ids() and not display_changed:
                self.rotate(tv, time.monotonic())
                return
            if not owned:
                self.state['original'] = current
                self.save()
            image, title = self.card(metadata, details)
            content = tv.upload(str(image), matte='none')
            old_album = self.state.get('album_image') or owned
            in_collage = self.state.get('view') == 'collage'
            self.state.setdefault('garbage', []).append(content)
            self.save()
            if not in_collage:
                self.select(tv, content)
            self.state['garbage'].remove(content)
            if old_album:
                self.state['garbage'].append(old_album)
            self.state.update(album_image=content, album=album, title=title, display_key=display_key, classical=details)
            if not in_collage:
                self.state['view'] = 'album'
            self.save()
            self.cleanup(tv)
            self.rotate(tv, time.monotonic())
            LOG.info('Displayed album: %s (%.1fs after detection)', title or 'local album', time.monotonic() - started)
        finally:
            tv.close()


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', required=True)
    parser.add_argument('--state-dir', required=True)
    parser.add_argument('--once', action='store_true')
    parser.add_argument('--restore', action='store_true')
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s')
    bridge = Bridge(json.loads(Path(args.config).read_text()), args.state_dir)
    if args.restore:
        bridge.restore()
        return
    if args.once:
        bridge.step()
        return
    def stop(*_):
        bridge.running = False
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    while bridge.running:
        delay = bridge.config['poll_seconds']
        try:
            bridge.step()
        except Exception as error:
            # Avoid logging URLs, tokens or SOAP responses on request failures.
            LOG.warning('Artwork update deferred: %s', type(error).__name__)
            delay = 60
        if bridge.collage_until is not None:
            delay = min(delay, max(.1, bridge.collage_until - time.monotonic()))
        deadline = time.monotonic() + delay
        while bridge.running and time.monotonic() < deadline:
            time.sleep(min(.25, max(0, deadline - time.monotonic())))
    try:
        bridge.restore()
    except Exception as error:
        LOG.warning('Restore pending: %s', type(error).__name__)


if __name__ == '__main__':
    main()
