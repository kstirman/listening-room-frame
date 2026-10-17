# Portable metadata for new imports

The Frame's reviewed enrichment database remains the source for display corrections and composition grouping. Player applications do not read that database. To carry approved corrections and artwork into both players, prepare tagged **new-import copies** before upload. Existing library files are outside this workflow.

`prepare.py` supports FLAC, WAV and AIFF with single-stream lossless integer audio. It needs the application's Python dependencies and ffmpeg/ffprobe. Unsupported formats must be reported as unresolved, never silently converted. Player support for custom classical/date tags varies; basic tags and embedded front covers are portable, but actual player display must be checked separately.

1. Preserve downloaded originals. Identify only files whose destination does not already exist. Research each recording and cover; keep uncertain dates explicitly qualified and provenance in the private plan/database.
2. Create a private JSON plan with a nonempty `tracks` list. Each row contains `path` relative to the source folder, original `sha256`, `review_status: "reviewed"`, evidence `source`, `reviewed_at`, and `fields`. Optional `artwork` contains a reviewed image `path` relative to source and its `sha256`. Use only approved fields, never an automatic dump of enrichment data. Preserve detailed per-field evidence in the enrichment store; the plan's source must identify that review.
3. Run `python intake/prepare.py --source /private/downloads --plan /private/reviewed-plan.json --output /private/unique-preparation`. Output must be new and separate from source. The tool stores a plan, untouched `originals/`, and tagged `prepared/` files. It checks tags and embedded art after writing, compares decoded PCM hashes, and writes `verified.json` only after the whole batch passes. Failures leave partial output for inspection; do not upload it or reuse that directory.
4. Upload only a fully verified `prepared/` batch without overwriting existing destinations. Verify remote files against **prepared** hashes; original hashes intentionally differ. Keep the original backups and plan durably outside the served library before deleting download/staging copies. Rollback uses these originals, with explicit review before replacing any indexed file.
5. Register the final library paths and tags in the existing inventory and enrichment store. Preserve work-group identifiers, source evidence and all earlier corrections. Embedded art does not replace the Frame database or previously reviewed artwork mappings. Install any needed album sidecars using the established reviewed-art workflow.
6. Rescan MinimServer and verify new tracks and artwork; then JPLAY Sync Data. In Audirvana, verify the watched-folder scan has discovered the new files and refreshed tags/art. Do not edit its database directly or assume that a MinimServer rescan refreshes Audirvana. Report an unobserved player refresh as pending.
7. Validate Frame resolution for the original local resource and simulated Audirvana identity, then representative renders. Resolver tests are not proof of either app's UI, the TV, or audible playback.

Supported fields: `title`, `album`, `artist`, `albumArtist`, `composer`, `conductor`, `work`, `movementName`, `orchestra`, `soloists`, `recordingDate`, `releaseDate`, `originalReleaseDate`, `compositionDate`. Existing unrelated tags and non-front artwork are retained. Only reviewed front artwork is replaced in prepared copies. Recording/release/original-release/composition dates remain distinct; qualified ID3 dates are stored verbatim in named custom fields instead of being silently truncated to a year. Standard work/grouping support varies across applications. Movement order and recording-specific composition IDs remain in the private Frame store; no track renumbering is performed.

A synthetic example row:

```json
{"path":"Album/01.flac","sha256":"<original SHA-256>","review_status":"reviewed","source":"Private per-field review reference","reviewed_at":"2026-01-01","fields":{"album":"Example album","recordingDate":"circa 1951","releaseDate":"2000"},"artwork":{"path":"cover.jpg","sha256":"<image SHA-256>"}}
```

Do not publish plans, original music, prepared files, artwork or verification manifests.
