# Permanent music metadata

Saved album names, track names, credits, relationships and researched dates do
not expire. The shared `music-catalog.sqlite3` is the durable library; memory
caches are only accelerators. Successful MusicBrainz responses and prior import
versions remain on disk indefinitely. Neither a restart nor a failed lookup
removes them. Back up the complete database using the catalog backup command.

JPLAY names are read from a copy of its local database. Exact provider album and
track IDs anchor the match, including when the renderer supplies only duration.
Existing reviewed editions take precedence. Imports fill gaps and preserve prior
values and incoming versions; conflicting corrections require review. This does
not change JPLAY, audio files, playback settings or the selected renderer.

Export on a computer with JPLAY and the optional `realm@20.2.0` Node package:

```sh
node tools/export_jplay.cjs /path/to/default.realm /private/jplay-names.json
python persistent_metadata.py --catalog /private/music-catalog.sqlite3 \
  --jplay /private/jplay-names.json
# Review the dry-run summary, then add --apply. A database backup is automatic.
```

The exporter opens only its disposable copy, exports an allowlist of music fields,
and excludes stream URLs, account settings, authentication and listening history.
The export is a snapshot, not a live JPLAY API. Repeat the import after JPLAY learns
new music; saved names remain usable on the display host without the exporting
computer. Both the export and the catalog are private and must not be committed.

Set `qobuz_metadata_enabled` to `false` to disable public Qobuz metadata requests.
Saved Qobuz editions and their identities remain usable. This flag does not
change playback or artwork selection. Set `musicbrainz_enabled` to `true` to
search MusicBrainz in the background for newly encountered JPLAY albums. It is
optional and needs no account. It uses at most one request per 1.1 seconds and
saves responses permanently. Searches do not block the artwork.

A search result is only a candidate. Automatic application requires a matching
Qobuz edition link plus a complete, uniquely correlated title/duration tracklist.
Otherwise the candidate is retained for review. Release dates never become
recording or composition dates; relationships do not imply influence. Discogs
release links from an exact MusicBrainz match are retained for local enrichment.

Failures and unresolved searches are recorded, not retried on every play. After
an outage, an explicit `--retry-musicbrainz ALBUM_ID --apply` clears only failure
markers (including provider access backoff); it preserves all facts. Restart the
artwork service to clear its in-memory attempt marker. Resolved responses are
never automatically refreshed due to age.

## Discogs and permanent storage

Discogs' [API terms](https://support.discogs.com/hc/en-us/articles/360009334593-API-Terms-of-Use)
include a six-hour freshness requirement. This integration therefore does not use
its API for indefinite offline storage. It supports selected releases from a
local CC0 catalog XML export instead:

```sh
python persistent_metadata.py --catalog /private/music-catalog.sqlite3 \
  --discogs-xml /private/releases.xml.gz --release-ids 123 456
```

Add `--apply` after reviewing the summary. This is a local import; it does not
download the full Discogs catalog or need a token. Images, marketplace/user data
and prose notes are excluded. Only exact linked releases with corroborated track
lists can supply album-level label, producer and engineer fields. Imported
track-specific credits remain available as evidence for review.

References: [MusicBrainz API](https://musicbrainz.org/doc/MusicBrainz_API),
[rate limits](https://musicbrainz.org/doc/MusicBrainz_API/Rate_Limiting).
