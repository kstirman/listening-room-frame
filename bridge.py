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
import sqlite3
import time
from html.parser import HTMLParser
from urllib.parse import urlparse
import layouts
import timing
import liner_notes
import idle_photos
from photo_stream import Stream
import local_match
import local_composition
import library_metadata
import auto_catalog
import cover_evidence
import source_outcomes
import music_catalog
import track_research
import persistent_metadata
import music_context
import programme_context
import reviewed_catalog
import classical
import dates
import safe_requests
from playback_text import limit_texts
from cover_images import normalized_cover
from roon_source import RoonSource
from sonos_source import SonosSource
from spotify_source import SpotifySource
from network_limits import fetch, json_value, xml_root

import requests
from samsungtvws.art.art import SamsungTVArt

LOG = logging.getLogger('frame-art')
SERVICE = 'urn:schemas-upnp-org:service:AVTransport:1'
ERROR_RETRY_SECONDS = 5
MAX_UPNP_METADATA_CODEPOINTS = 131_072
MAX_UPNP_METADATA_UTF8_BYTES = 262_144
UPNP_DISPLAY_FIELDS = ('title', 'album', 'artist', 'albumArtist', 'composer',
                       'conductor', 'orchestra', 'performer', 'soloist', 'genre',
                       'date', 'work', 'group', 'movement', 'movementName',
                       'movementNumber')
TV_RESPONSE_BYTES = 100_000
SOAP_RESPONSE_BYTES = 2_000_000
BROWSE_TOTAL_BYTES = 8_000_000
BROWSE_MAX_ITEMS = 10_000
BROWSE_MAX_PAGES = 20
CATALOG_RESPONSE_BYTES = 2_000_000
ARTWORK_RESPONSE_BYTES = 20_000_000


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
                self.structured.append(json_value(''.join(self.json_text),
                                       max_bytes=1_000_000, max_depth=32,
                                       max_nodes=20_000, max_text=500_000))
            except (ValueError, requests.RequestException):
                pass


def _metadata_values(root):
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
    fields = [name for name in UPNP_DISPLAY_FIELDS if name in result]
    for name, value in zip(fields, limit_texts([result[name] for name in fields])):
        result[name] = value
    return result


def metadata_values(xml):
    if isinstance(xml, bytes):
        if len(xml) > MAX_UPNP_METADATA_UTF8_BYTES:
            return {}
    elif isinstance(xml, str):
        sample = xml[:MAX_UPNP_METADATA_CODEPOINTS + 1]
        if (len(sample) > MAX_UPNP_METADATA_CODEPOINTS
                or len(sample.encode('utf-8')) > MAX_UPNP_METADATA_UTF8_BYTES):
            return {}
    else:
        return {}
    root = xml_root(xml, max_bytes=SOAP_RESPONSE_BYTES, max_depth=32,
                    max_nodes=10_000, max_text=1_500_000)
    return _metadata_values(root)


class ArtworkUnavailable(Exception):
    """No verified artwork is available for the current album."""


class Bridge:
    def __init__(self, config, root):
        self.config = config
        source = config.get('source', 'upnp')
        if source not in ('upnp', 'roon', 'sonos', 'spotify'):
            raise ValueError('Unsupported playback source')
        self.roon = RoonSource(config['roon_state_dir']) if source == 'roon' else None
        self.sonos = SonosSource(config.get('sonos_uid')) if source == 'sonos' else None
        self.spotify = SpotifySource(config.get('spotify_token_file')) if source == 'spotify' else None
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.state_file = self.root / 'state.json'
        self.state = json.loads(self.state_file.read_text()) if self.state_file.exists() else {}
        self.session = safe_requests.Session()
        self.session.headers['User-Agent'] = 'ListeningRoomArtwork/1.0'
        self.stopped_at = (time.monotonic() - config.get("idle_seconds", 120)
                           if self.state.get("view") == "photo" else None)
        self.next_photo = 0
        self.blocked_album = None
        self.running = True
        self.pages = {}
        self.page_failures = {}
        self.detail_cache = {}
        self.local_compositions = local_composition.Catalog()
        self.local_matches = local_match.Index()
        self.music_catalog = music_catalog.Store(self.root / 'music-catalog.sqlite3')
        try:
            self.music_catalog.migrate(self.root / 'catalog-cache.sqlite3')
            self.music_catalog.sync_documents(self.config, Path(__file__).parent)
        except Exception as error:
            LOG.warning('Music catalog import deferred: %s', type(error).__name__)
        self.auto_catalog = auto_catalog.Resolver(cache_path=self.music_catalog.path)
        self.independent_catalog = persistent_metadata.Resolver(
            self.music_catalog.path, enabled=config.get("musicbrainz_enabled", False))
        self.recording_research = track_research.Research(self.independent_catalog.cache, self.independent_catalog.provider)
        self.photo_stream = Stream(config) if config.get("photo_stream_enabled", False) else None
        self.local_index = {}
        self.index_at = 0
        self.last_tv_check = 0
        self.next_collage = time.monotonic() + self.config.get('collage_interval_seconds', 120)
        self.collage_until = 0 if self.state.get('view') == 'collage' else None
        self.state.setdefault('history', [])
        (self.root / 'covers').mkdir(exist_ok=True)
        self.normalize_history()
        self.evidence_archive = None
        if config.get('cover_evidence_root') and config.get('cover_evidence_mount'):
            self.evidence_archive = cover_evidence.Archive(config['cover_evidence_root'], config['cover_evidence_mount'])
            self.evidence_backfill()



    def preserve_cover_evidence(self, entry):
        archive = getattr(self, 'evidence_archive', None)
        if archive is None or not entry.get('key', '').startswith('qobuz:'):
            return True
        try:
            path = self.root / entry['cover']
            if path.is_symlink() or path.resolve().parent != (self.root / 'covers').resolve():
                raise ValueError('Unsafe cached cover path')
            archive.preserve(entry['key'], path.read_bytes(), entry.get('artwork_url'))
            return True
        except Exception as error:
            LOG.warning('Cover evidence preservation unavailable: %s', type(error).__name__)
            return False

    def evidence_unlink(self, entry):
        # Pending JSON is private state, not authority to delete arbitrary files.
        name = entry.get('cover')
        if not isinstance(name, str):
            return False
        path = self.root / name
        covers = self.root / 'covers'
        if (path.is_symlink() or covers.is_symlink() or
                path.resolve().parent != covers.resolve()):
            return False
        references = {e.get('cover') for e in self.state.get('history', [])}
        references.add(getattr(self, '_evidence_transient', {}).get('cover'))
        if any(isinstance(ref, str) and (self.root / ref).resolve() == path.resolve() for ref in references):
            return False
        path.unlink(missing_ok=True)
        return True

    def evidence_backfill(self):
        pending = []
        history = self.state.get('history', [])
        for entry in history + self.state.get('evidence_pending', []):
            if not self.preserve_cover_evidence(entry):
                if not any(e.get('cover') == entry.get('cover') for e in pending):
                    pending.append(entry)
            else:
                self.evidence_unlink(entry)
        self.state['evidence_pending'] = pending
        self.save()

    def evidence_evict(self, removed):
        if getattr(self, 'evidence_archive', None) is None:
            for entry in removed:
                self.evidence_unlink(entry)
            return
        pending = self.state.setdefault('evidence_pending', [])
        for entry in removed:
            if self.preserve_cover_evidence(entry):
                self.evidence_unlink(entry)
            elif not any(e.get('cover') == entry.get('cover') for e in pending):
                pending.append(entry)
        self.save()

    def save(self):
        temp = self.state_file.with_suffix('.tmp')
        temp.write_text(json.dumps(self.state, indent=2))
        temp.replace(self.state_file)

    def soap(self, action):
        body = f'<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/"><s:Body><u:{action} xmlns:u="{SERVICE}"><InstanceID>0</InstanceID></u:{action}></s:Body></s:Envelope>'
        url = self.config.get('renderer_control_url') or self.config['renderer'] + '/AVTransport/control'
        response = fetch(
            self.session.post, url, trusted_origins=(url,),
            max_bytes=SOAP_RESPONSE_BYTES, total_seconds=8, data=body,
            headers={'SOAPAction': f'"{SERVICE}#{action}"', 'Content-Type': 'text/xml'}, timeout=8)
        root = xml_root(response.content, max_bytes=SOAP_RESPONSE_BYTES, max_depth=32,
                        max_nodes=10_000, max_text=1_500_000)
        return {el.tag.split('}')[-1]: el.text for el in root.iter()}

    def playback(self):
        self._media_fingerprint = 'missing'
        if self.roon or self.sonos or self.spotify:
            status, metadata = (self.roon or self.sonos or self.spotify).playback()
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
            pages = 0
            response_bytes = 0
            deadline = time.monotonic() + 30
            service = 'urn:schemas-upnp-org:service:ContentDirectory:1'
            while True:
                if pages >= BROWSE_MAX_PAGES:
                    raise ValueError('Library browse page limit exceeded')
                body = f'<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/"><s:Body><u:Browse xmlns:u="{service}"><ObjectID>0$items</ObjectID><BrowseFlag>BrowseDirectChildren</BrowseFlag><Filter>*</Filter><StartingIndex>{start}</StartingIndex><RequestedCount>500</RequestedCount><SortCriteria></SortCriteria></u:Browse></s:Body></s:Envelope>'
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise requests.Timeout('Library browse exceeded total deadline')
                reply = fetch(self.session.post, self.config['minim_control'],
                              max_bytes=SOAP_RESPONSE_BYTES,
                              total_seconds=min(15, remaining), data=body,
                              headers={'SOAPAction': f'"{service}#Browse"',
                                       'Content-Type': 'text/xml'}, timeout=15,
                              trusted_origins=(self.config['minim_control'],))
                pages += 1
                response_bytes += len(reply.content)
                if response_bytes > BROWSE_TOTAL_BYTES:
                    raise ValueError('Library browse byte limit exceeded')
                envelope = xml_root(reply.content, max_bytes=SOAP_RESPONSE_BYTES,
                                    max_depth=32, max_nodes=20_000,
                                    max_text=1_900_000)
                response = {el.tag.split('}')[-1]: el.text for el in envelope.iter()}
                if not all(response.get(name) is not None
                           for name in ('Result', 'NumberReturned', 'TotalMatches')):
                    raise ValueError('Invalid library browse response')
                if not re.fullmatch(r'[0-9]{1,8}', response['NumberReturned']) or not re.fullmatch(
                        r'[0-9]{1,8}', response['TotalMatches']):
                    raise ValueError('Invalid library browse counters')
                count = int(response['NumberReturned'])
                total = int(response['TotalMatches'])
                if count > 500 or total > BROWSE_MAX_ITEMS:
                    raise ValueError('Library browse item limit exceeded')
                result = xml_root(response['Result'], max_bytes=SOAP_RESPONSE_BYTES,
                                  max_depth=32, max_nodes=20_000,
                                  max_text=1_900_000)
                items = list(result)
                if len(items) != count:
                    raise ValueError('Library browse counter mismatch')
                for item in items:
                    values = _metadata_values(item)
                    resource = values.get('resource')
                    if resource:
                        index[resource] = values
                next_start = start + count
                if next_start > total or next_start > BROWSE_MAX_ITEMS:
                    raise ValueError('Invalid library browse progress')
                if count == 0:
                    if start < total:
                        raise ValueError('Library browse made no progress')
                    break
                if next_start <= start:
                    raise ValueError('Library browse made no progress')
                start = next_start
                if start >= total:
                    break
            self.local_index = index
            self.index_at = time.monotonic()
        return self.local_index.get(uri, {})

    @staticmethod
    def album_key(metadata):
        if metadata.get('source') in ('roon', 'sonos', 'spotify'):
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
        url = f"http://{self.config['tv']}:8001/api/v2/"
        response = fetch(self.session.get, url, trusted_origins=(url,),
                         max_bytes=TV_RESPONSE_BYTES, total_seconds=4,
                         same_origin_redirects=0, timeout=4)
        device = response.json(max_bytes=TV_RESPONSE_BYTES, max_depth=16,
                               max_nodes=2_000, max_text=20_000)['device']
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

    @staticmethod
    def qobuz_cover_url(url):
        try:
            parsed = urlparse(url or '')
            return (parsed.scheme == 'https' and parsed.hostname == 'static.qobuz.com'
                    and parsed.path.startswith('/images/covers/')
                    and parsed.username is None and parsed.password is None
                    and parsed.port in (None, 443))
        except ValueError:
            return False

    def reviewed_edition_match(self, metadata):
        album_id = metadata.get('album_id')
        if not album_id:
            return None, {}
        rows = self.music_catalog.edition(album_id) or {}
        verified = {key: row for key, row in rows.items()
                    if row.get('_catalog_album_id') == album_id
                    and row.get('_edition_verified') == album_id}
        selected = (metadata.get('object_id') or '').rsplit('/', 1)[-1]
        if selected in verified:
            return selected, verified[selected]
        # Ordinal-only captures have no provider track IDs. Require an exact
        # duration and, when supplied, title within a complete verified edition.
        # Select only a unique candidate; never use a near-duration match.
        import catalog_pages
        duration = timing.seconds(metadata.get('duration_seconds'))
        if (not duration or not rows or len(verified) != len(rows)
                or catalog_pages.needs_refresh(rows)
                or any(str(row.get('_catalog_expected_tracks')) != str(len(rows)) for row in rows.values())
                or any(not timing.seconds(row.get('duration_seconds')) for row in rows.values())):
            return None, {}
        matches = [(key, row) for key, row in verified.items()
                   if timing.seconds(row.get('duration_seconds')) == duration]
        title = " ".join((metadata.get('title') or '').split()).casefold()
        if title:
            matches = [(key, row) for key, row in matches
                       if " ".join((row.get('title') or '').split()).casefold() == title]
        if len(matches) == 1:
            return matches[0]
        cached = self.cached_ordinal_match(metadata, verified, duration)
        if cached[0] is not None or matches:
            return cached
        # A reviewed playback duration is evidence, not a tolerance. Require
        # the supplied title and track artist, and reject all ambiguous names.
        def normalized(value):
            return ' '.join(value.split()).casefold() if isinstance(value, str) else ''
        artist = normalized(metadata.get('artist'))
        if not title or not artist:
            return None, {}
        named = [(key, row) for key, row in verified.items()
                 if normalized(row.get('title')) == title
                 and normalized(row.get('artist')) == artist]
        if len(named) != 1:
            return None, {}
        key, row = named[0]
        if (str(row.get('_source_ordinal')) != key
                or row.get('_catalog_track_id')
                or timing.seconds(row.get('_observed_duration_seconds')) != duration
                or (metadata.get('version') and normalized(metadata['version']) != normalized(row.get('version')))):
            return None, {}
        return key, row

    def cached_ordinal_match(self, metadata, rows, duration):
        """Corroborate an ordinal capture using saved exact JPLAY identity only."""
        aid = metadata.get('album_id')
        match = re.fullmatch(r'qobuz/track/(\d+)', metadata.get('object_id') or '')
        # Mixed captures need a complete unique global source ordering. Only
        # ordinal-keyed rows are fallback candidates; provider IDs keep priority.
        ordinals = [str(row.get('_source_ordinal')) for row in rows.values()]
        if (not match or len(set(ordinals)) != len(rows)
                or set(ordinals) != {str(n) for n in range(1, len(rows) + 1)}):
            return None, {}
        resolver = getattr(self, 'independent_catalog', None)
        cache = getattr(resolver, 'cache', None)
        if cache is None:
            return None, {}
        try:
            album = cache.get('jplay', aid)
        except (OSError, ValueError, sqlite3.Error):
            return None, {}
        if (not isinstance(album, dict) or album.get('album_id') != aid
                or str(album.get('expected_tracks')) != str(len(rows))
                or not isinstance(album.get('provenance'), dict)
                or album['provenance'].get('source') != 'JPLAY local music database'):
            return None, {}
        tracks = album.get('tracks')
        track = tracks.get(match[1]) if isinstance(tracks, dict) else None
        if not isinstance(track, dict) or timing.seconds(track.get('duration_seconds')) != duration:
            return None, {}
        def normalized(value):
            return ' '.join(value.split()).casefold() if isinstance(value, str) else ''
        title = normalized(track.get('title'))
        version = normalized(track.get('version'))
        full_title = normalized(str(track.get('title')) + ' (' + track['version'] + ')') if version else title
        incoming = normalized(metadata.get('title'))
        if metadata.get('version') and normalized(metadata['version']) != version:
            return None, {}
        if not title or (incoming and incoming not in {title, full_title}):
            return None, {}
        candidates = [(key, row) for key, row in rows.items()
                      if timing.seconds(row.get('duration_seconds')) == duration
                      and normalized(row.get('title')) == full_title]
        # Track numbers reset on multi-disc releases. On a single disc the
        # known source position must agree even when title/timing are unique.
        if str(album.get('volumes')) == '1':
            position = str(track.get('track_number'))
            if position not in ordinals:
                return None, {}
            candidates = [(key, row) for key, row in candidates
                          if str(row.get('_source_ordinal')) == position]
        if len(candidates) != 1:
            return None, {}
        key, row = candidates[0]
        if (str(row.get('_source_ordinal')) != key
                or (row.get('_catalog_track_id') and str(row['_catalog_track_id']) != match[1])
                or (row.get('version') and normalized(row['version']) != version)):
            return None, {}
        return key, row

    def reviewed_edition_row(self, metadata):
        return self.reviewed_edition_match(metadata)[1]

    def reviewed_edition_album(self, metadata):
        album_id = metadata.get('album_id')
        rows = (self.music_catalog.edition(album_id) or {}) if album_id else {}
        verified = [row for row in rows.values()
                    if row.get('_catalog_album_id') == album_id
                    and row.get('_edition_verified') == album_id]
        result = {}
        for field in ('album', 'albumArtist', 'artist', '_album_art_url'):
            values = {row[field] for row in verified if row.get(field)}
            if len(values) == 1:
                result[field] = values.pop()
        return result

    def source_outcome(self, album_id):
        if not hasattr(self, 'page_outcomes'):
            self.page_outcomes = {}
        if album_id not in self.page_outcomes:
            read = getattr(self.music_catalog, 'source_outcome', None)
            self.page_outcomes[album_id] = read(album_id) if callable(read) else None
        return self.page_outcomes[album_id]

    def record_source_outcome(self, album_id, outcome):
        self.page_outcomes[album_id] = outcome
        write = getattr(self.music_catalog, 'put_source_outcome', None)
        if callable(write):
            write(album_id, outcome)

    def album_page(self, album_id):
        if not self.config.get('qobuz_metadata_enabled', True):
            raise requests.RequestException('Qobuz metadata disabled; using permanent local catalog')
        previous = self.source_outcome(album_id)
        now = time.time()
        due = previous and previous.get('category') == 'transient' and now >= previous.get('next_retry_at', float('inf'))
        if album_id in self.pages and not due:
            return self.pages[album_id]
        if previous and previous.get('category') != 'complete' and not due:
            raise requests.RequestException('Catalog source pending or deferred')
        try:
            deadline = time.monotonic() + 30
            response = fetch(self.session.get,
                             'https://www.qobuz.com/us-en/album/-/' + album_id,
                             max_bytes=CATALOG_RESPONSE_BYTES, total_seconds=15,
                             same_origin_redirects=2, timeout=15)
            import catalog_pages
            page, outcome = catalog_pages.complete_result(
                response.text, album_id, self.session.get, deadline=deadline,
                initial_bytes=len(response.content))
        except requests.RequestException as error:
            outcome = source_outcomes.scheduled(dict(source_outcomes.failure(error), stage='album'), previous, now)
            self.record_source_outcome(album_id, outcome)
            raise
        self.record_source_outcome(album_id, source_outcomes.scheduled(outcome, previous, now))
        if len(self.pages) >= 8:
            self.pages.pop(next(iter(self.pages)))
        self.pages[album_id] = page
        return page

    def fill_qobuz_gaps(self, metadata, fields, research=False):
        """Reuse an exact release response across tracks and restarts."""
        album_id = metadata['album_id']
        if not re.fullmatch(r'[A-Za-z0-9]{1,64}', album_id):
            return fields
        complete = (fields.get('album') and (fields.get('albumArtist') or fields.get('artist'))
                    and fields.get('title') and timing.seconds(fields.get('duration_seconds')))
        cache = self.independent_catalog.cache
        source = 'qobuz-fallback'
        record = cache.get(source, album_id)
        if record is None:
            if complete and not research:
                return fields
            try:
                page = self.album_page(album_id)
            except requests.RequestException:
                return fields
            products = [item for item in auto_catalog.records(page)
                        if item.get('@type') == 'Product' and str(item.get('sku')) == album_id]
            if len(products) != 1:
                self.record_source_outcome(album_id, {'category': 'pending_identity_conflict'})
                self.pages.pop(album_id, None)
                return fields
            product = products[0]
            brand = product.get('brand')
            header = {'album': product.get('name'), 'albumArtist': brand.get('name') if isinstance(brand, dict) else None,
                      'releaseDate': product.get('releaseDate') or product.get('datePublished')}
            record = {'album': header, 'tracks': auto_catalog.parsed_rows(page, album_id)}
            cache.write([(source, album_id, record)])
        header = dict(record['album'])
        if fields.get('artist') or fields.get('albumArtist'):
            header.pop('albumArtist', None)
        result = persistent_metadata.merge_missing(fields, header)
        track_id = re.fullmatch(r'qobuz/track/([0-9]+)', metadata.get('object_id', ''))
        row = record['tracks'].get(track_id[1], {}) if track_id else {}
        if (row and (not fields.get('title') or auto_catalog.canonical(fields['title']) == auto_catalog.canonical(row.get('title')))
                and (not fields.get('duration_seconds') or timing.seconds(fields['duration_seconds']) == timing.seconds(row.get('duration_seconds')))):
            result = persistent_metadata.merge_missing(result, {k:v for k,v in row.items() if not k.startswith('_')})
        return result

    def details(self, metadata):
        if metadata.get('source') == 'spotify':
            # Spotify's release date and artist credits do not identify recording
            # sessions or classical roles. Never send these IDs to another catalog.
            return {name: metadata.get(name) for name in (
                'object_id', 'title', 'album', 'artist', 'duration_seconds', 'releaseDate', 'source_url', 'spotify_cover')}
        if metadata.get('source') in ('roon', 'sonos'):
            field = metadata['source'] + '_lines'
            return {field: metadata[field], 'duration_seconds': timing.seconds(metadata.get('duration_seconds'))}
        track = metadata.get('object_id') or metadata.get('resource') or json.dumps(metadata, sort_keys=True)
        self.music_catalog.refresh(self.config, Path(__file__).parent)
        cache_first = self.config.get('qobuz_cache_first', False)
        automatic = (not metadata.get('album_id') and
                     self.config.get('qobuz_metadata_enabled', True) and
                     self.config.get('auto_catalog', True) and
                     (urlparse(metadata.get('resource', '')).path.startswith('/audirvana/') or
                      self.config.get('auto_catalog_other_upnp', False)))
        cache_key = (track, metadata.get('title', ''), timing.seconds(metadata.get('duration_seconds')), metadata.get('album', ''), metadata.get('artist', ''))
        if metadata.get('resource') and self.config.get('library_root') and not metadata.get('album_id'):
            cache_key += (int(time.monotonic() // 60),)
        cache_key += (self.music_catalog.cache_revision(),)
        if self.config.get('recording_research_enabled'):
            cache_key += (int(time.time() // 3600),)
        if metadata.get('album_id'):
            outcome = self.source_outcome(metadata['album_id']) or {}
            deadline = outcome.get('next_retry_at')
            cache_key += (outcome.get('category'), deadline, bool(deadline and time.time() >= deadline))
        if automatic:
            cache_key += (int(time.monotonic() // 10), self.auto_catalog.revision)
        if cache_key in self.detail_cache:
            return self.detail_cache[cache_key]
        enriched = dict(metadata)
        try:
            saved = self.independent_catalog.lookup(metadata)
            enriched = persistent_metadata.merge_missing(enriched, saved)
        except (OSError, ValueError, sqlite3.Error):
            LOG.warning('Independent metadata temporarily unavailable')
        work_timing = None
        reviewed_display_title = None
        catalog_unavailable = False
        local_status = None
        local_resource = None
        if not metadata.get('album_id') and self.config.get('library_root') and urlparse(metadata.get('resource', '')).path.startswith('/audirvana/'):
            try:
                match, local_status = self.music_catalog.match_local(metadata, self.config['library_root'], self.config.get('library_resource_prefix', '/minimserver/*/AudirvanaLibrary/'))
                if not match and local_status != 'local_ambiguous':
                    match, local_status = self.local_matches.lookup(metadata, self.config['library_root'], self.config.get('library_resource_prefix', '/minimserver/*/AudirvanaLibrary/'))
                if match:
                    local_resource, fields = match
                    enriched.update(fields)
            except Exception as error:
                local_status = 'local_unavailable'
                LOG.warning('Local recording match unavailable: %s', type(error).__name__)
        if metadata.get('album_id'):
            ordered = self.music_catalog.edition(metadata['album_id'])
            import catalog_pages
            if not cache_first and self.config.get('qobuz_metadata_enabled', True) and (not ordered or catalog_pages.needs_refresh(ordered)):
                prior_ordered = ordered
                try:
                    page = self.album_page(metadata['album_id'])
                    fresh = auto_catalog.parsed_rows(page, metadata['album_id'])
                    merged = source_outcomes.merge_refresh(ordered, fresh, metadata['album_id'])
                    if merged is None:
                        old_outcome = self.source_outcome(metadata['album_id']) or {}
                        self.record_source_outcome(metadata['album_id'], dict(old_outcome, category='pending_identity_conflict', next_retry_at=None))
                        raise requests.RequestException('Catalog refresh identity requires review')
                    ordered = merged
                    album_parser = MetaParser(); album_parser.feed(page)
                    album, artist = metadata.get('album'), metadata.get('artist')
                    for record in album_parser.structured:
                        if isinstance(record, dict) and record.get('@type') in ('Product', 'MusicAlbum'):
                            value = record.get('releaseDate') or record.get('datePublished')
                            if value:enriched.setdefault('releaseDate', value)
                        if isinstance(record, dict) and record.get('@type') == 'Product':
                            album = record.get('name') or album
                            brand = record.get('brand', {})
                            if isinstance(brand, dict):artist = brand.get('name') or artist
                    if ordered != prior_ordered:
                        self.music_catalog.put_edition(metadata['album_id'], ordered, album, artist)
                except requests.RequestException:
                    catalog_unavailable = True
                    LOG.warning('Catalog enrichment unavailable; using current metadata')
            ordered = ordered or {}
            selected = track.rsplit('/', 1)[-1]
            row = ordered.get(selected)
            if not row:
                selected, row = self.reviewed_edition_match(metadata)
            if row:
                enriched.update(row)
                # A reviewed spelling correction changes presentation only, after
                # exact-edition track selection; never use it as a matching alias.
                canonical = row.get('_canonical_title')
                if (row.get('_edition_verified') == metadata['album_id']
                        and row.get('_catalog_album_id') == metadata['album_id']
                        and isinstance(canonical, str) and canonical.strip()):
                    reviewed_display_title = canonical.strip()
                work_timing = timing.composition(ordered, selected)
            if cache_first and self.config.get('qobuz_metadata_enabled', True):
                enriched = persistent_metadata.merge_missing(enriched, self.reviewed_edition_album(metadata))
                enriched.update(self.music_catalog.encountered_album(enriched))
                enriched = persistent_metadata.merge_missing(enriched, self.music_catalog.encountered_track(metadata))
                try:
                    enriched = self.fill_qobuz_gaps(metadata, enriched)
                except (OSError, sqlite3.Error, ValueError):
                    LOG.warning('Qobuz fallback unavailable')
        elif metadata.get('resource') and self.config.get('library_root'):
            saved_local = self.music_catalog.local(local_resource or metadata['resource'], self.config['library_root'], self.config.get('library_resource_prefix', '/minimserver/*/AudirvanaLibrary/'))
            if saved_local:
                fields, work_timing = saved_local
                enriched.update(fields)
                local_status = 'local_matched'
            else:
                try:
                    enriched.update(classical.file_tags(metadata['resource'], self.config['library_root'], self.config.get('library_resource_prefix', '/minimserver/*/AudirvanaLibrary/')))
                except Exception as error:
                    LOG.warning('File metadata unavailable: %s', type(error).__name__)
                # Reviewed metadata remains usable even if ffprobe cannot read a file.
                try:
                    enriched.update(library_metadata.lookup(metadata['resource'], self.config['library_root'], self.config.get('library_resource_prefix', '/minimserver/*/AudirvanaLibrary/')))
                except Exception as error:
                    LOG.warning('Saved metadata unavailable: %s', type(error).__name__)
            if work_timing is None and not metadata.get('album_id') and metadata.get('resource') and self.config.get('library_root'):
                try:
                    work_timing = self.local_compositions.context(local_resource or metadata['resource'], self.config['library_root'], enriched, self.config.get('library_resource_prefix', '/minimserver/*/AudirvanaLibrary/'))
                except Exception as error:
                    LOG.warning('Local composition unavailable: %s', type(error).__name__)
        local_priority = local_status in ('local_matched', 'local_ambiguous', 'local_unavailable')
        named_network = not cache_first or not (enriched.get('album') and
                        (enriched.get('artist') or enriched.get('albumArtist')) and
                        enriched.get('title') and timing.seconds(enriched.get('duration_seconds')))
        if work_timing is None and not metadata.get('album_id') and not local_priority:
            try:
                match = reviewed_catalog.lookup(metadata, self.config.get('reviewed_catalog_albums', []), self.album_page, self.music_catalog, allow_network=named_network)
                if match:
                    row, work_timing = match
                    enriched.update(row)
            except Exception as error:
                LOG.warning('Reviewed catalog unavailable: %s', type(error).__name__)
        catalog_status = "catalog_id" if metadata.get("album_id") and row else None
        if local_priority:
            catalog_status = local_status
        elif automatic and work_timing is None:
            match, catalog_status = self.auto_catalog.lookup(metadata) if named_network else self.auto_catalog.lookup(metadata, allow_network=False)
            if match:
                row, work_timing = match
                enriched.update(row)
        elif automatic:
            catalog_status = 'matched'
        notes = {}
        if self.config.get('liner_notes_enabled', True):
            notes = liner_notes.from_credits(enriched.get('credits', ''))
            notes.update(liner_notes.extract(enriched))
            notes.update(liner_notes.lookup_data(enriched, self.music_catalog.document('liner', {}) or {}))
            notes.update(liner_notes.lookup(enriched, self.config))
        if self.config.get('recording_research_enabled') and local_status != 'local_ambiguous':
            local_context = music_context.lookup(enriched, self.music_catalog.document('music_context', {}))
            need_context = not (local_context or notes.get('note') or programme_context.extract(enriched))
            need_credits = not all(notes.get(k) for k in ('personnel','songwriters','producer','engineer'))
            facts = self.recording_research.lookup(metadata, enriched, need_credits, need_context,
                self.fill_qobuz_gaps if self.config.get('qobuz_metadata_enabled', True) else None,
                self.config.get('musicbrainz_enabled', False))
            sourced = persistent_metadata.merge_missing(liner_notes.extract(facts), liner_notes.from_credits(facts.get('credits','')))
            notes = persistent_metadata.merge_missing(notes, sourced)
        details = classical.normalize(enriched)
        programme = programme_context.extract(enriched)
        if programme:
            details = dict(details or {}, programme_context=programme)
        if any(notes.get(k) for k in ('personnel', 'songwriters', 'recordingLocation', 'label', 'producer', 'engineer', 'note')):
            details = dict(details or {}, liner_notes=notes)
        if catalog_status:
            details = dict(details or {}, catalog_status=catalog_status)
        track_title = reviewed_display_title or enriched.get('title') or metadata.get('title')
        if track_title:
            details = dict(details or {}, display_track=track_title)
        duration = timing.seconds(enriched.get('duration_seconds'))
        if duration: details = dict(details or {}, duration_seconds=duration)
        if work_timing: details = dict(details or {}, composition=work_timing)
        date_metadata = dict(enriched, **notes)
        if enriched.get('_catalog_track_id'):
            date_metadata['object_id'] = 'qobuz/track/' + enriched['_catalog_track_id']
        date_fields = dates.extract(date_metadata, details,
            work_registry=self.music_catalog.document('work_dates'),
            recording_registry=self.music_catalog.document('recording_dates'))
        if date_fields:
            details = dict(details or {}, **date_fields)
        connections = music_context.lookup(dict(enriched, **(details or {})),
            self.music_catalog.document('music_context', {}))
        if connections:
            details = dict(details or {}, connections=connections)
        # Reviewed edition captions also fill sparse Qobuz renderer metadata.
        reviewed_row = self.reviewed_edition_album(metadata)
        caption_source = dict(enriched)
        if metadata.get('album_id') and not reviewed_row:
            caption_source.update(self.music_catalog.encountered_album(enriched))
        caption_source.update({k: v for k, v in reviewed_row.items() if v})
        captions = {'display_album': caption_source.get('album'),
                    'display_artist': caption_source.get('albumArtist') or caption_source.get('artist')}
        captions = {k: v for k, v in captions.items() if v}
        if captions:
            details = dict(details or {}, **captions)
        if len(self.detail_cache) >= 128:
            self.detail_cache.pop(next(iter(self.detail_cache)))
        try:
            self.music_catalog.remember(metadata, details)
        except Exception as error:
            LOG.warning('Music catalog observation deferred: %s', type(error).__name__)
        if not catalog_unavailable and (not metadata.get('album_id') or reviewed_row):
            self.detail_cache[cache_key] = details
        return details

    def card(self, metadata, details):
        if metadata.get('source') == 'spotify':
            cover = self.root / 'spotify-cover.jpg'
            # One current cover only; Spotify content does not enter the history
            # collage, record rack or permanent cross-provider metadata catalog.
            identity = (metadata['source_album_key'], metadata['spotify_cover'])
            if getattr(self, '_spotify_cover_identity', None) != identity or not cover.exists():
                content = self.spotify.cover(metadata)
                with normalized_cover(io.BytesIO(content)) as source:
                    source.save(cover, quality=95)
                self._spotify_cover_identity = identity
            output = self.root / 'current-cover.jpg'
            layouts.spotify(cover, metadata, output)
            return output, metadata.get('album', '')
        album = self.album_key(metadata)
        entry = next((e for e in self.state['history'] if e['key'] == album), None)
        if entry is None and getattr(self, '_evidence_transient', {}).get('key') == album:
            entry = self._evidence_transient
        refreshed_art = (entry is not None and self.config.get('library_root')
                         and urlparse(metadata.get('resource', '')).path.startswith('/audirvana/')
                         and entry.get('artwork_url') != self.local_art_url(metadata))
        if album != self.state.get('album') or entry is None or refreshed_art:
            image, title = self.cover(metadata)
            entry = next((e for e in self.state['history'] if e['key'] == album), None)
            if entry is None and getattr(self, '_evidence_transient', {}).get('key') == album:
                entry = self._evidence_transient
        else:
            image, title = self.root / 'current-cover.jpg', entry['title']
        if metadata.get('source') in ('roon', 'sonos') and entry:
            layouts.roon(self.root / entry['cover'], metadata[metadata['source'] + '_lines'], image, duration_seconds=(details or {}).get('duration_seconds'))
        elif details and details.get('work') and entry:
            layouts.classical(self.root / entry['cover'], details, image)
        elif entry and ((details or {}).get('liner_notes') or (details or {}).get('connections') or (details or {}).get('programme_context')):
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
        inferred_cover = False
        trusted_art_hosts = ()
        title = metadata.get('album', '')
        artist = metadata.get('albumArtist') or metadata.get('artist', '')
        if metadata.get('source') == 'roon':
            content = self.roon.cover(metadata)
        elif metadata.get('source') == 'sonos':
            content = self.sonos.cover(metadata)
        elif album_id:
            if self.config.get('qobuz_cache_first'):
                entry = next((e for e in self.state['history'] if e['key'] == self.album_key(metadata)
                              and (self.root / e['cover']).is_file()), None)
                if entry:
                    output = self.root / 'current-cover.jpg'
                    layouts.current(self.root / entry['cover'], entry['title'], entry.get('artist', ''), output)
                    self.state['artist'] = entry.get('artist', '')
                    return output, entry['title']
            reviewed = self.reviewed_edition_album(metadata)
            title = reviewed.get('album') or title
            artist = reviewed.get('albumArtist') or reviewed.get('artist') or artist
            url = reviewed.get('_album_art_url', '')
            # Verified saved editions must remain useful when the public page is blocked.
            if not self.qobuz_cover_url(url) and not self.config.get('qobuz_cache_first'):
                parser = MetaParser()
                try:
                    parser.feed(self.album_page(album_id))
                except requests.RequestException:
                    pass
                url = parser.meta.get('og:image', '')
                title = reviewed.get('album') or parser.meta.get('og:title', '').removesuffix(' - Qobuz') or title
                for record in parser.structured:
                    if isinstance(record, dict) and record.get('@type') == 'Product':
                        title = reviewed.get('album') or record.get('name') or title
                        brand = record.get('brand', {})
                        if isinstance(brand, dict) and not reviewed:
                            artist = brand.get('name') or artist
            if not self.qobuz_cover_url(url):
                url = metadata.get('albumArtURI', '')
            if not self.qobuz_cover_url(url):
                entry = next((e for e in self.state['history']
                              if e['key'] == self.album_key(metadata)
                              and (self.root / e['cover']).is_file()), None)
                if entry:
                    output = self.root / 'current-cover.jpg'
                    layouts.current(self.root / entry['cover'], entry['title'], entry.get('artist', ''), output)
                    self.state['artist'] = entry.get('artist', '')
                    return output, entry['title']
                # Qobuz's observed CDN layout encodes the exact edition ID.
                # This is a candidate image only, never evidence for captions
                # or track identity. Validate its response before saving it.
                if not isinstance(album_id, str) or not re.fullmatch(r'[A-Za-z0-9]{4,64}', album_id):
                    raise ArtworkUnavailable('Current album artwork unavailable')
                url = (f'https://static.qobuz.com/images/covers/{album_id[-2:]}/'
                       f'{album_id[-4:-2]}/{album_id}_600.jpg')
                inferred_cover = True
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
            if local_cover:
                trusted_art_hosts = self.config['local_art_hosts']
        if metadata.get('source') not in ('roon', 'sonos'):
            try:
                response = fetch(self.session.get, url,
                                 max_bytes=ARTWORK_RESPONSE_BYTES,
                                 total_seconds=15,
                                 same_origin_redirects=0 if inferred_cover else 2,
                                 timeout=15,
                                 **({"trusted_hosts": trusted_art_hosts} if trusted_art_hosts else {}))
                if inferred_cover and (response.status_code != 200 or
                        response.headers.get('Content-Type', '').split(';')[0].strip().lower() not in ('image/jpeg', 'image/png', 'image/webp')):
                    raise ArtworkUnavailable('Current album artwork unavailable')
                content = response.content
            except requests.RequestException as error:
                if inferred_cover:
                    raise ArtworkUnavailable('Current album artwork unavailable') from error
                raise
        if len(content) > ARTWORK_RESPONSE_BYTES:
            if inferred_cover:
                raise ArtworkUnavailable('Current album artwork unavailable')
            raise ValueError('Cover exceeds size limit')
        try:
            cover = normalized_cover(io.BytesIO(content))
        except ValueError as error:
            if inferred_cover:
                raise ArtworkUnavailable('Current album artwork unavailable') from error
            raise
        key = self.album_key(metadata)
        filename = 'covers/' + hashlib.sha256(key.encode()).hexdigest() + '.jpg'
        if getattr(self, 'evidence_archive', None) and key.startswith('qobuz:'):
            self.evidence_backfill()
            encoded = io.BytesIO()
            cover.save(encoded, format='JPEG', quality=95)
            content = encoded.getvalue()
            filename = 'covers/' + hashlib.sha256(key.encode()).hexdigest() + '-' + hashlib.sha256(content).hexdigest() + '.jpg'
            durable = False
            try:
                self.evidence_archive.preserve(key, content, url)
                durable = True
            except Exception as error:
                LOG.warning('Cover evidence preservation unavailable: %s', type(error).__name__)
            cache_bytes = sum(p.stat().st_size for p in (self.root / 'covers').glob('*.jpg') if p.is_file())
            # Existing unarchived bytes are never evicted for lack of archive.
            # During an outage, new covers above the bounded cache budget are
            # transient display images; retention failure remains explicit.
            if not durable and cache_bytes + len(content) > 16 * 1024 * 1024:
                transient = self.root / 'evidence-transient.jpg'
                transient.write_bytes(content)
                self._evidence_transient = {'key': key, 'cover': transient.name,
                    'title': title, 'artist': artist, 'artwork_url': None}
                self.state['evidence_status'] = 'unavailable_cache_budget_reached'
                self.save()
                output = self.root / 'current-cover.jpg'
                layouts.current(transient, title, artist, output)
                return output, title
            self.state['evidence_status'] = 'preserved' if durable else 'pending'
        cover.save(self.root / filename, quality=95)
        history = [entry for entry in self.state['history'] if entry['key'] != key]
        history.insert(0, {'key': key, 'cover': filename, 'title': title, 'artist': artist,
                           'artwork_url': url if metadata.get('source') not in ('roon', 'sonos') else None})
        removed = history[self.config.get('history_limit', 28):]
        if getattr(self, 'evidence_archive', None):
            removed += [e for e in self.state['history'] if e['key'] == key and e['cover'] != filename]
        self.state['history'] = history[:self.config.get('history_limit', 28)]
        self.state['artist'] = artist
        self.save()
        self.evidence_evict(removed)
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
        if self.config.get('source') == 'spotify':
            if self.state.get('view') == 'collage' and self.state.get('album_image'):
                self.select(tv, self.state['album_image'])
                self.state['view'] = 'album'
                self.collage_until = None
                self.save()
            self.next_collage = float('inf')
            return
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
        now = time.monotonic()
        # Recheck persisted manual holds: an interrupted upload can become
        # current after a later attempt has replaced the pending selection.
        if self.state.get('idle_photo_blocked') and now - self.last_tv_check < 30:
            return
        if self.state.get('view') == 'photo' and now < self.next_photo and now - self.last_tv_check < 30:
            return
        themes = getattr(self.photo_stream, 'themes', None) if self.photo_stream and self.config.get('photo_themes_enabled') else None
        presentation = None
        if themes:
            try:presentation = themes.presentation(self.state)
            except Exception:
                LOG.warning('Photo theme presentation unavailable')
        photos = self.photo_stream.available() if self.photo_stream else []
        if not photos:
            photos = idle_photos.load(self.config)
        if self.photo_stream and photos:
            fresh = [p for p in photos if p['key'] not in self.state.get('photo_recent', [])]
            if not fresh and not presentation:
                # Hold the current card while the worker prepares fresh choices.
                return
            photos = fresh
        if not photos and not presentation:
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
            # These IDs were saved immediately after our own uploads. A lost
            # select response must not turn one into a permanent manual hold.
            owned.update(self.state.get('garbage', []))
            if self.state.get('owned') and current not in owned:
                # A manual art choice lasts until the next music session.
                self.state['idle_photo_blocked'] = True
                self.save()
                return
            if self.state.pop('idle_photo_blocked', None):
                self.save()
                LOG.info('Recovered idle rotation after interrupted artwork selection')
            if self.state.get('view') == 'photo' and now < self.next_photo:
                return
            if not self.state.get('owned'):
                self.state['original'] = current
                self.save()
            output = self.root / 'idle-photo.jpg'
            # A broken cache entry cannot trap the rotation or overwrite TV art.
            if presentation:
                photos = presentation['photos']
                group = list(range(len(photos)));index = 0
                layouts.photo_montage(photos, presentation['recipe'], output, presentation['layout'])
            else:
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
            if themes and len(themes.eligible([photos[i] for i in group], themes.data()[2])) != len(group):
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
            if themes:
                receipt = dict(id=content, photos=[{k:v for k,v in photos[i].items() if k!='path'} for i in group],
                               presentation={k:v for k,v in presentation.items() if k!='photos'} if presentation else None)
                self.state['photo_receipts'] = (self.state.get('photo_receipts', []) + [receipt])[-32:]
                try:
                    themes.record(content, receipt['photos'], receipt['presentation'])
                except Exception as error:
                    LOG.warning('Photo display receipt deferred: %s', type(error).__name__)
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
        changed = signature != getattr(self, '_last_playback_signature', None)
        encounter = getattr(getattr(self, 'music_catalog', None), 'encounter', None)
        if status == 'PLAYING' and callable(encounter):
            # Providers can fill in names after sending a sparse playback ID.
            # Retry failed storage without tying persistence to playback changes.
            fields = ('album_id', 'object_id', 'album', 'artist', 'albumArtist',
                      'title', 'composer', 'genre', 'duration_seconds', 'source')
            intake = (signature, auto_catalog.artwork_edition(metadata.get('albumArtURI')),
                      fingerprint(json.dumps(
                          {key: metadata.get(key) for key in fields}, sort_keys=True)))
            now = time.monotonic()
            if ((changed or intake != getattr(self, '_last_encounter_saved', None)) and
                    (intake != getattr(self, '_last_encounter_attempt', None) or
                     now >= getattr(self, '_encounter_retry_at', 0))):
                self._last_encounter_attempt = intake
                if encounter(metadata) is True:
                    self._last_encounter_saved = intake
                    self._encounter_retry_at = 0
                else:
                    self._encounter_retry_at = now + 30
        if changed:
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
            try:
                image, title = self.card(metadata, details)
            except ArtworkUnavailable:
                # An unidentified new album must not leave the previous album showing.
                self.restore()
                return
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
