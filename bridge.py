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
import timing
import liner_notes
import idle_photos
from photo_stream import Stream
import local_match
import local_composition
import library_metadata
import auto_catalog
import reviewed_catalog
import classical
import dates
from roon_source import RoonSource
from sonos_source import SonosSource

import requests
from PIL import Image, ImageOps
from samsungtvws.art.art import SamsungTVArt

LOG = logging.getLogger('frame-art')
SERVICE = 'urn:schemas-upnp-org:service:AVTransport:1'
ERROR_RETRY_SECONDS = 5


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
            if not match:
                # BubbleUPnP may prefix albums with its browse/search context.
                # Require a Qobuz root and a terminal album/track pair; never
                # infer identity from a proxy URL or fetch the context as a URL.
                match = re.fullmatch(
                    r'qobuz/(?:[a-zA-Z0-9_-]+/)*albums/([a-zA-Z0-9]+)/[0-9]+',
                    item.get('id', ''))
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
            duration = timing.seconds(item.get('duration'))
            if duration: result['duration_seconds'] = duration
    return result


class Bridge:
    def __init__(self, config, root):
        self.config = config
        source = config.get('source', 'upnp')
        if source not in ('upnp', 'roon', 'sonos'):
            raise ValueError('Unsupported playback source')
        self.roon = RoonSource(config['roon_state_dir']) if source == 'roon' else None
        self.sonos = SonosSource(config.get('sonos_uid')) if source == 'sonos' else None
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.state_file = self.root / 'state.json'
        self.state = json.loads(self.state_file.read_text()) if self.state_file.exists() else {}
        self.session = requests.Session()
        self.session.headers['User-Agent'] = 'ListeningRoomArtwork/1.0'
        self.stopped_at = None
        self.next_photo = 0
        self.blocked_album = None
        self.running = True
        self.pages = {}
        self.detail_cache = {}
        self.local_compositions = local_composition.Catalog()
        self.local_matches = local_match.Index()
        self.auto_catalog = auto_catalog.Resolver(cache_path=self.root / 'catalog-cache.sqlite3')
        self.photo_stream = Stream(config) if config.get("photo_stream_enabled", False) else None
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
        self._media_fingerprint = 'missing'
        if self.roon or self.sonos:
            status, metadata = (self.roon or self.sonos).playback()
            self._media_fingerprint = metadata.get('object_id', 'missing')
            return status, metadata
        status = self.soap('GetTransportInfo').get('CurrentTransportState')
        if status != 'PLAYING':
            return status, {}
        position = self.soap('GetPositionInfo')
        metadata = position.get('TrackMetaData')
        result = metadata_values(metadata) if metadata and metadata != 'NOT_IMPLEMENTED' else {}
        duration = timing.seconds(position.get('TrackDuration'))
        if duration: result['duration_seconds'] = duration
        uri = position.get('TrackURI') or result.get('resource', '')
        self._media_fingerprint = hashlib.sha256(uri.encode()).hexdigest()[:12] if uri else 'missing'
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
        if metadata.get('source') in ('roon', 'sonos'):
            return metadata.get('source_album_key')
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
        if metadata.get('source') in ('roon', 'sonos'):
            field = metadata['source'] + '_lines'
            return {field: metadata[field], 'duration_seconds': timing.seconds(metadata.get('duration_seconds'))}
        track = metadata.get('object_id') or metadata.get('resource') or json.dumps(metadata, sort_keys=True)
        automatic = (not metadata.get('album_id') and bool(classical.normalize(metadata)) and
                     self.config.get('auto_catalog', True) and
                     (urlparse(metadata.get('resource', '')).path.startswith('/audirvana/') or
                      self.config.get('auto_catalog_other_upnp', False)))
        cache_key = (track, metadata.get('title', ''), timing.seconds(metadata.get('duration_seconds')), metadata.get('album', ''), metadata.get('artist', ''))
        if metadata.get('resource') and self.config.get('library_root') and not metadata.get('album_id'):
            cache_key += (int(time.monotonic() // 60),)
        if automatic:
            cache_key += (int(time.monotonic() // 10), self.auto_catalog.revision)
        if cache_key in self.detail_cache:
            return self.detail_cache[cache_key]
        enriched = dict(metadata)
        work_timing = None
        local_status = None
        local_resource = None
        if not metadata.get('album_id') and self.config.get('library_root') and urlparse(metadata.get('resource', '')).path.startswith('/audirvana/'):
            try:
                match, local_status = self.local_matches.lookup(metadata, self.config['library_root'], self.config.get('library_resource_prefix', '/minimserver/*/AudirvanaLibrary/'))
                if match:
                    local_resource, fields = match
                    enriched.update(fields)
            except Exception as error:
                local_status = 'local_unavailable'
                LOG.warning('Local recording match unavailable: %s', type(error).__name__)
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
            ordered = classical.catalog(parser.tracks)
            enriched.update(ordered.get(track.rsplit('/', 1)[-1], {}))
            work_timing = timing.composition(ordered, track.rsplit('/', 1)[-1])
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
        if not metadata.get('album_id') and metadata.get('resource') and self.config.get('library_root'):
            try:
                work_timing = self.local_compositions.context(local_resource or metadata['resource'], self.config['library_root'], enriched, self.config.get('library_resource_prefix', '/minimserver/*/AudirvanaLibrary/'))
            except Exception as error:
                LOG.warning('Local composition unavailable: %s', type(error).__name__)
        local_priority = local_status in ('local_matched', 'local_ambiguous', 'local_unavailable')
        if work_timing is None and not metadata.get('album_id') and not local_priority:
            try:
                match = reviewed_catalog.lookup(metadata, self.config.get('reviewed_catalog_albums', []), self.album_page)
                if match:
                    row, work_timing = match
                    enriched.update(row)
            except Exception as error:
                LOG.warning('Reviewed catalog unavailable: %s', type(error).__name__)
        catalog_status = None
        if local_priority:
            catalog_status = local_status
        elif automatic and work_timing is None:
            match, catalog_status = self.auto_catalog.lookup(metadata)
            if match:
                row, work_timing = match
                enriched.update(row)
        elif automatic:
            catalog_status = 'matched'
        notes = {}
        if self.config.get('liner_notes_enabled', True):
            notes = liner_notes.lookup(metadata, self.config)
            notes.update(liner_notes.extract(enriched))
        details = classical.normalize(enriched)
        if any(notes.get(k) for k in ('personnel', 'songwriters', 'recordingLocation', 'label', 'producer', 'engineer', 'note')):
            details = dict(details or {}, liner_notes=notes)
        if catalog_status:
            details = dict(details or {}, catalog_status=catalog_status)
        track_title = enriched.get('title') or metadata.get('title')
        if track_title:
            details = dict(details or {}, display_track=track_title)
        duration = timing.seconds(enriched.get('duration_seconds'))
        if duration: details = dict(details or {}, duration_seconds=duration)
        if work_timing: details = dict(details or {}, composition=work_timing)
        date_fields = dates.extract(dict(enriched, **notes), details)
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
        self.detail_cache[cache_key] = details
        return details

    def card(self, metadata, details):
        album = self.album_key(metadata)
        entry = next((e for e in self.state['history'] if e['key'] == album), None)
        refreshed_art = (entry is not None and self.config.get('library_root')
                         and urlparse(metadata.get('resource', '')).path.startswith('/audirvana/')
                         and entry.get('artwork_url') != self.local_art_url(metadata))
        if album != self.state.get('album') or entry is None or refreshed_art:
            image, title = self.cover(metadata)
            entry = next((e for e in self.state['history'] if e['key'] == album), None)
        else:
            image, title = self.root / 'current-cover.jpg', entry['title']
        if metadata.get('source') in ('roon', 'sonos') and entry:
            layouts.roon(self.root / entry['cover'], metadata[metadata['source'] + '_lines'], image, duration_seconds=(details or {}).get('duration_seconds'))
        elif details and details.get('work') and entry:
            layouts.classical(self.root / entry['cover'], details, image)
        elif entry and (details or {}).get('liner_notes'):
            title = (details or {}).get('display_album') or title
            artist = (details or {}).get('display_artist') or entry.get('artist', '')
            layouts.liner(self.root / entry['cover'], title, artist, image, details)
        elif entry:
            title = (details or {}).get('display_album') or title
            artist = (details or {}).get('display_artist') or entry.get('artist', '')
            layouts.current(self.root / entry['cover'], title, artist, image, date_line=dates.display_line(details), duration_seconds=(details or {}).get('duration_seconds'), track_title=(details or {}).get('display_track', ''))
        return image, title

    def local_art_url(self, metadata):
        """Reuse reviewed artwork only after a unique local recording match."""
        overrides = self.config.get('local_art_overrides', {})
        resources = [(metadata.get('resource', ''), metadata.get('album', ''))]
        if self.config.get('library_root') and urlparse(resources[0][0]).path.startswith('/audirvana/'):
            try:
                match, status = self.local_matches.lookup(metadata, self.config['library_root'], self.config.get('library_resource_prefix', '/minimserver/*/AudirvanaLibrary/'))
                if match:
                    resource, fields = match
                    resources = [(resource, album) for album in
                                 {metadata.get('album', ''), fields.get('album', '')} if album]
            except Exception as error:
                LOG.warning('Local artwork match unavailable: %s', type(error).__name__)
        choices = set()
        for resource, album in resources:
            directory = urlparse(resource).path.rsplit('/', 1)[0]
            # Synthesized local resources use percent escapes; MinimServer may use *XX.
            for path in {directory, re.sub(r'%([0-9A-Fa-f]{2})', r'*\1', directory)}:
                key = 'local:' + path + ':' + album
                for candidate in (key, self.canonical_album_key(key)):
                    if overrides.get(candidate): choices.add(overrides[candidate])
        if len(choices) == 1:
            return choices.pop()
        if not choices:
            return overrides.get(self.album_key(metadata), metadata.get('albumArtURI', ''))
        return metadata.get('albumArtURI', '')

    def cover(self, metadata):
        album_id = metadata.get('album_id')
        title = metadata.get('album', '')
        artist = metadata.get('albumArtist') or metadata.get('artist', '')
        if metadata.get('source') == 'roon':
            content = self.roon.cover(metadata)
        elif metadata.get('source') == 'sonos':
            content = self.sonos.cover(metadata)
        elif album_id:
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
            url = self.local_art_url(metadata)
            parsed = urlparse(url)
            # Audirvana supplies Qobuz cover metadata without Qobuz item IDs.
            # Accept only the cover CDN path, never the audio resource URL.
            qobuz_cover = (parsed.scheme == 'https'
                           and parsed.hostname == 'static.qobuz.com'
                           and parsed.path.startswith('/images/covers/')
                           and parsed.username is None and parsed.password is None
                           and parsed.port in (None, 443))
            local_cover = (parsed.scheme in ('http', 'https')
                           and parsed.hostname in self.config['local_art_hosts'])
            if not (qobuz_cover or local_cover):
                raise ValueError('No supported cover URL')
        if metadata.get('source') not in ('roon', 'sonos'):
            r = self.session.get(url, timeout=15)
            r.raise_for_status()
            content = r.content
        if len(content) > 20_000_000:
            raise ValueError('Cover exceeds size limit')
        cover = ImageOps.exif_transpose(Image.open(io.BytesIO(content))).convert('RGB')
        key = self.album_key(metadata)
        filename = 'covers/' + hashlib.sha256(key.encode()).hexdigest() + '.jpg'
        cover.save(self.root / filename, quality=95)
        history = [entry for entry in self.state['history'] if entry['key'] != key]
        history.insert(0, {'key': key, 'cover': filename, 'title': title, 'artist': artist,
                           'artwork_url': url if metadata.get('source') not in ('roon', 'sonos') else None})
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
        return {self.state.get(name) for name in ('owned', 'album_image', 'collage_image', 'photo_image')} - {None}

    def select(self, tv, content):
        self.phase = 'tv-select'
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
        self.phase = 'tv-cleanup'
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
        self.phase = 'restore'
        owned = self.owned_ids()
        if self.state.get('pending'):
            owned.add(self.state['pending'])
        if not owned:
            return
        tv = self.television()
        if tv is None:
            return
        try:
            self.phase = 'tv-state'
            if tv.get_artmode() != 'on':
                return
            current = tv.get_current()['content_id']
            if current in owned:
                tv.select_image(self.state['original'])
                if tv.get_current()['content_id'] != self.state['original']:
                    raise RuntimeError('Restore not confirmed')
                LOG.info('Original artwork restored')
            self.state.setdefault('garbage', []).extend(owned)
            for field in ('owned', 'album', 'album_image', 'collage_image', 'collage_revision', 'photo_image', 'view', 'pending'):
                self.state.pop(field, None)
            self.collage_until = None
            self.next_collage = time.monotonic() + self.config.get('collage_interval_seconds', 120)
            self.save()
            self.cleanup(tv)
        finally:
            tv.close()

    def show_idle_photo(self):
        """Rotate cached photos only while idle; preserve manual TV selections."""
        if self.state.get('idle_photo_blocked'):
            return
        now = time.monotonic()
        if self.state.get('view') == 'photo' and now < self.next_photo and now - self.last_tv_check < 30:
            return
        photos = self.photo_stream.available() if self.photo_stream else []
        if not photos:
            photos = idle_photos.load(self.config)
        if self.photo_stream and photos:
            fresh = [p for p in photos if p['key'] not in self.state.get('photo_recent', [])]
            if not fresh:
                # Hold the current card while the worker prepares fresh choices.
                return
            photos = fresh
        if not photos:
            self.restore()
            return
        tv = self.television()
        if tv is None:
            return
        try:
            if tv.get_artmode() != 'on':
                return
            current = tv.get_current()['content_id']
            self.last_tv_check = time.monotonic()
            owned = self.owned_ids() | {self.state.get('pending')}
            if self.state.get('owned') and current not in owned:
                # A manual art choice lasts until the next music session.
                self.state['idle_photo_blocked'] = True
                self.save()
                return
            if self.state.get('view') == 'photo' and now < self.next_photo:
                return
            if not self.state.get('owned'):
                self.state['original'] = current
                self.save()
            output = self.root / 'idle-photo.jpg'
            # A broken cache entry cannot trap the rotation or overwrite TV art.
            excluded = set()
            for _ in range(min(len(photos), 5)):
                index = idle_photos.choose(photos, self.state, excluded)
                excluded.add(index)
                try:
                    group = idle_photos.portrait_group(photos, index, self.state)
                    if len(group) > 1:
                        layouts.photo_panels([photos[i] for i in group], output)
                    else:
                        layouts.photo(photos[index]['path'], photos[index]['caption'], output)
                    break
                except (OSError, ValueError):
                    LOG.warning('Skipping unreadable cached photo')
            else:
                self.save()
                return
            self.phase = 'photo-upload'
            content = tv.upload(str(output), matte='none')
            old = self.state.get('photo_image')
            self.state.setdefault('garbage', []).append(content)
            self.save()
            self._selecting_photo = True
            try:
                self.select(tv, content)
            finally:
                self._selecting_photo = False
            self.state['garbage'].remove(content)
            if old:
                self.state['garbage'].append(old)
            for sibling in group[1:]:
                idle_photos.remember(photos, sibling, self.state)
            idle_photos.remember(photos, index, self.state)
            self.state.update(photo_image=content, view='photo')
            self.next_photo = time.monotonic() + idle_photos.interval(self.config)
            self.save()
            self.cleanup(tv)
            LOG.info('Idle photo displayed')
        finally:
            tv.close()

    def observe_playback(self, status, metadata):
        # Keep URLs, controller IDs and listening details out of diagnostics.
        def fingerprint(value):
            return hashlib.sha256(value.encode()).hexdigest()[:12] if value else 'missing'
        signature = (status,
                     fingerprint(metadata.get('object_id') or metadata.get('resource')),
                     fingerprint(self.album_key(metadata)),
                     getattr(self, '_media_fingerprint', fingerprint(metadata.get('resource'))))
        if signature != getattr(self, '_last_playback_signature', None):
            LOG.info('Playback changed: state=%s track=%s album=%s media=%s', *signature)
            self._last_playback_signature = signature

    def step(self):
        if self.photo_stream:
            self.photo_stream.tick(self.state)
        self.phase = 'playback-query'
        status, metadata = self.playback()
        self.observe_playback(status, metadata)
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
            if status in ('STOPPED', 'PAUSED_PLAYBACK', 'PAUSED', 'NO_MEDIA_PRESENT') and time.monotonic() - self.stopped_at >= self.config['idle_seconds']:
                if self.config.get('idle_photos_enabled', False):
                    self.show_idle_photo()
                else:
                    self.restore()
            return
        self.stopped_at = None
        if self.state.pop('idle_photo_blocked', None):
            self.save()
        album = self.album_key(metadata)
        if not album:
            self.restore()
            return
        if album == self.blocked_album:
            return
        self.phase = 'metadata-lookup'
        details = self.details(metadata)
        display_key = album + ':' + json.dumps(details, sort_keys=True, ensure_ascii=False)
        display_changed = self.state.get('view') == 'photo' or self.state.get('display_key', album + ':null') != display_key
        now = time.monotonic()
        due = (now >= self.collage_until if self.collage_until is not None else now >= self.next_collage)
        if (album == self.state.get('album') and self.state.get('owned')
                and now - self.last_tv_check < 30 and not due and not display_changed):
            return
        started = time.monotonic()
        self.phase = 'tv-connect'
        tv = self.television()
        if tv is None:
            return
        try:
            self.phase = 'tv-state'
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
            self.phase = 'card-render'
            image, title = self.card(metadata, details)
            self.phase = 'tv-upload'
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
                old_photo = self.state.pop('photo_image', None)
                if old_photo:
                    self.state['garbage'].append(old_photo)
                    self.next_collage = time.monotonic() + self.config.get('collage_interval_seconds', 120)
            self.save()
            self.cleanup(tv)
            self.rotate(tv, time.monotonic())
            LOG.info('Displayed album: %s (%.1fs after detection)', title or 'local album', time.monotonic() - started)
        finally:
            tv.close()


def run_bounded(action, seconds=120):
    """Bound an entire operation, including continuously active socket waits.

    Linux main-thread entry point only. Exit on expiry rather than unwinding
    through potentially blocked TV cleanup; the user service restarts us.
    """
    def expired(_signum, frame):
        # Locations only: source lines/locals could expose URLs or credentials.
        try:
            locations = []
            while frame is not None and len(locations) < 8:
                code = frame.f_code
                locations.append(f'{Path(code.co_filename).name}:{frame.f_lineno}:{code.co_name}')
                frame = frame.f_back
            message = (f'Artwork operation timed out ({action.__name__}); restarting. '
                       + 'Stack: ' + ' > '.join(reversed(locations)) + '\n')
            os.write(2, message.encode('utf-8', errors='replace'))
        finally:
            os._exit(1)

    previous = signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        return action()
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


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
        run_bounded(bridge.restore, 30)
        return
    if args.once:
        run_bounded(bridge.step)
        return
    def stop(*_):
        bridge.running = False
        # Finish or exit before the service manager forces an abort.
        remaining, _ = signal.getitimer(signal.ITIMER_REAL)
        if remaining > 30:
            signal.setitimer(signal.ITIMER_REAL, 30)
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    while bridge.running:
        delay = bridge.config['poll_seconds']
        try:
            run_bounded(bridge.step)
        except Exception as error:
            # Avoid logging URLs, tokens or SOAP responses on request failures.
            LOG.warning('Artwork update deferred: %s phase=%s; retry in %ss',
                        type(error).__name__, getattr(bridge, 'phase', 'unknown'),
                        ERROR_RETRY_SECONDS)
            delay = ERROR_RETRY_SECONDS
        if bridge.collage_until is not None:
            delay = min(delay, max(.1, bridge.collage_until - time.monotonic()))
        deadline = time.monotonic() + delay
        while bridge.running and time.monotonic() < deadline:
            time.sleep(min(.25, max(0, deadline - time.monotonic())))
    try:
        run_bounded(bridge.restore, 30)
    except Exception as error:
        LOG.warning('Restore pending: %s', type(error).__name__)


if __name__ == '__main__':
    main()
