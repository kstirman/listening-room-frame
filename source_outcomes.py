"""Bounded source outcomes, independent of captions or verified metadata."""
import requests
import timing
import time
from email.utils import parsedate_to_datetime

TRANSIENT = {408, 425, 429, 500, 502, 503, 504}


def failure(error):
    response = getattr(error, 'response', None)
    status = response.status_code if response is not None else None
    if status is not None:
        category = 'transient' if status in TRANSIENT else ('denied' if status in (401, 403, 451) else 'pending_http')
    else:
        category = 'transient' if isinstance(error, (requests.Timeout, requests.ConnectionError)) else 'pending_parse'
    result = {'category': category, 'http_status': status}
    if response is not None:
        value = response.headers.get('Retry-After', '')
        try:
            seconds = int(value) if value.isdigit() else max(0, int(parsedate_to_datetime(value).timestamp() - time.time()))
            if seconds > 86400:
                result['category'] = 'pending_http'
            else:
                result['retry_after_seconds'] = seconds
        except (ValueError, TypeError, OverflowError):
            pass
    return result


def scheduled(outcome, previous, now):
    result = dict(outcome, attempted_at=now, source_kind='qobuz_public_album')
    attempts = int((previous or {}).get('transient_attempts', 0))
    if outcome['category'] == 'transient':
        attempts += 1
        if attempts >= 3:
            result['category'] = 'transient_exhausted'
        else:
            result['next_retry_at'] = now + max(60 * 2 ** (attempts - 1), outcome.get('retry_after_seconds', 0))
    result['transient_attempts'] = attempts
    return result


def merge_refresh(old, fresh, album_id):
    """Never transplant reviewed fields by ordinal alone or shrink old evidence."""
    if not old:
        return fresh
    if not fresh or len(fresh) < len(old):
        return None
    result = {key: dict(row) for key, row in fresh.items()}
    for key, row in old.items():
        candidate = fresh.get(key)
        if not candidate or any(r.get('_catalog_album_id') != album_id for r in (row, candidate)):
            return None
        if row.get('title') != candidate.get('title') or not timing.seconds(row.get('duration_seconds')) or timing.seconds(row.get('duration_seconds')) != timing.seconds(candidate.get('duration_seconds')):
            return None
        if row.get('_catalog_track_id') != candidate.get('_catalog_track_id'):
            return None
        if not row.get('_catalog_track_id') and not (row.get('_edition_verified') == album_id
                and candidate.get('_edition_verified') == album_id
                and row.get('_source_ordinal') is not None
                and row.get('_source_ordinal') == candidate.get('_source_ordinal')):
            return None
        result[key].update(row)
        for field in ('_catalog_complete', '_catalog_expected_tracks'):
            if field in candidate:
                result[key][field] = candidate[field]
    return result
