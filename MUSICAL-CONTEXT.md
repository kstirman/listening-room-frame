# Artwork and musical connections

All music cards preserve a complete, proportional cover in a 2080-pixel box on the 3840 × 2160 canvas, with a 40-pixel outer margin. Metadata occupies the side area and cannot shrink the artwork. Classical work, movement and relative-duration bars remain alongside it. One sourced connection can appear beside the cover; the listening console retains additional connections, links and full available liner credits.

Reviewed connections are private records imported into the existing SQLite music catalog. Add `music_context_file` to a direct UPnP bridge configuration or supply it through the setup API. The file is reread by the catalog's existing background refresh. No web search or model call blocks artwork, and no automatic research job is started.

Example schema below uses fictional records. Replace them with verified facts and a real evidence URL before setting a row to `reviewed`:

```json
{
  "albums": [{
    "album": "Exact album title",
    "artist": "Exact artist credit",
    "connections": [{
      "kind": "influence",
      "scope": "album",
      "review_status": "draft",
      "evidence": "explicit_attribution",
      "target_title": "Earlier album",
      "target_artist": "Earlier artist",
      "explanation": "A concise original account of the documented connection.",
      "source_title": "Artist interview",
      "source_url": "https://example.org/interview",
      "reviewed_at": "2026-10-31"
    }]
  }]
}
```

Supported kinds are `influence`, `earlier_version`, `shared_musicians`, `development`, `quotation` and `comparison`. Influence requires `explicit_attribution`; resemblance and shared credits do not establish it. Other claims use `documented` evidence. Editorial suggestions use `comparison` with `editorial_comparison` and are visibly labeled **Listening comparison**.

Album/artist text must match exactly after case and whitespace normalization. Ambiguous duplicate records produce no connection. Album-scope claims concern the album generally. A recording or track claim requires an exact `album_id` or `source_album_key` on its album record. A track claim additionally requires `track_id` matching playback `object_id`. Composition claims require an exact normalized `work` in both the connection and resolved playback metadata. This prevents a relationship about a composition from silently becoming a claim about a performance.

Each row requires a source title, HTTPS link, review date and nonempty explanation. The source must actually support the statement; schema validation cannot establish truth. Add corrections to the private file and retain the supporting evidence. Keep local records, library identities and listening history out of public commits.

This first implementation reads reviewed UPnP/local-library relationships. Roon/Sonos retain their supplied display lines. Spotify stays within its own current-track attribution path; Spotify data does not feed this catalog or generate influence claims. Automatic album research and a full-library relationship population are not included.
