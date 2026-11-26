# Catalog adoption and rollback

The existing music catalog stores provider editions, track research, protected corrections and Frame associations. Portable status reports values observed in local files. A difference between a reviewed correction and an embedded value stays visible as a proposal. For release title, album artist and disc and track order, the Frame reads observed values. Protected composer, session and work corrections can still inform the display.

## Adopting a collection

Use the healthy, verified canonical mount at its original absolute path. Prepare the complete verified v2 music snapshot using the collection recovery tool. Obtain its SHA-256 from an independently verified generation. The manifest records format `listening-room-music/v2`, scope equal to the canonical folder name, `coverage=audio-and-companions`, `complete=true`, the path count, and file rows with original paths prefixed by the scope, byte counts and SHA-256. Choose a fresh checkpoint filename outside the served library on the dedicated recovery disk. Allow space for one copy of the catalog. In the NUC layout, run from the deployed Frame source using its Python environment.

```sh
python library_authority.py --database "$catalog" --root "$collection" adopt \
  --frame-config "$frame_config" --checkpoint "$checkpoint" \
  --snapshot "$verified_manifest" --snapshot-sha256 "$verified_digest"
```

Adoption reads the catalog's present files and checks their bytes against the recovery generation. A mismatch stops the migration. Replacement storage must present the expected files and recorded stamps for initial adoption. A restore with differing stamps before adoption needs verified reconciliation from its matching catalog generation. Once adopted, matching content hashes allow restored files to retain their research after a timestamp change. The migration saves a checked SQLite preimage, preserves unbound historical sidecar records, then commits the authority change in a single transaction. Additional adoption runs recognize the existing marker. The Frame's catalog refresh, adoption and review commands use the same ordinary-user lock at `~/.local/state/listening-room/library.lock`.

## Working with reviewed facts

```sh
python library_authority.py --database "$catalog" --root "$collection" status "release/disc 1/track.flac"
python library_authority.py --database "$catalog" --root "$collection" pending
python library_authority.py --database "$catalog" --root "$collection" review "$review_json"
python library_authority.py --database "$catalog" --root "$collection" undo "$batch"
```

Per-track review JSON uses `tracks[relative_path].fields` and `tracks[relative_path].provenance`. Each proposed value needs a source. Reviewed and protected fields survive a blank or conflicting fill. The edit returns the batch ID for targeted undo. Status includes observed tags, requests with their origin, the baseline file digest and any exact publication receipt. A changed file at a known path enters the persistent identity queue if its bytes fail verification. Preserve the differing file and use the recorded recovery generation to investigate.

For an exact artwork mapping or a relationship, liner note or date record, use `edit-record EDIT.json`. The edit supplies `kind`, `source`, `before` and `after`. A dictionary such as artwork or recording dates also needs its exact `key`. Use JSON null for the old value on a new record. The kinds are `artwork`, `liner`, `music_context`, `catalog_repairs`, `work_dates` and `recording_dates`. For an array, supply the complete old and new record. The command compares the before-image in the same transaction and returns a batch ID for `undo-record BATCH`. Context and liner records can use `source_album_key` for an exact local release or `album_id` for an exact provider edition. Track-specific liner records can use `track_id`. A verified new local release uses the stable edition recorded by intake. Its artwork can be associated using the same edition as the mapping key and a reviewed cover locator supported by the existing Frame configuration.

Keep this catalog and the current collection generation together in recovery. Save the previous program files and verified migrations as a pair. A full rollback uses the checkpoint taken at adoption with its matching source, during a maintenance window after preserving newer edits. Historic flat sidecar files and source documents remain recovery evidence. The adopted Frame reads the catalog version of the shared records.
