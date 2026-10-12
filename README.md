# Listening Room Frame

Display the album you're listening to on a Samsung The Frame TV, with a rotating collage of your 28 most recent albums. The TV stays in Art Mode while audio plays through your existing hi-fi.

A Python service reads playback information from a UPnP renderer, a selected Sonos room, or the experimental Roon extension, finds artwork, renders a 4K image, and sends it to the Frame's local Art Mode interface. No iPad screen mirroring or TV audio playback is involved.

## Choose your playback source

**JPLAY is not required.** The bridge observes the renderer's UPnP AVTransport interface; it does not connect to JPLAY or require an iPad. JPLAY and BubbleUPnP are controllers that can start playback on that renderer. Other controllers may work if their renderer supplies compatible track metadata, but have not all been tested.

| Setup | Current support |
| --- | --- |
| JPLAY or BubbleUPnP controlling a compatible UPnP renderer | Supported by the existing UPnP input |
| Another controller using UPnP AVTransport | Potentially compatible; verify its renderer and metadata |
| Qobuz through that UPnP renderer | Album and track enrichment supported |
| Local files served by MinimServer | Supported; file-level enrichment is optional |
| Roon Server | Experimental selected-output support; [setup and validation limits](roon/README.md) |
| Sonos room | Experimental read-only input; [setup and validation limits](SONOS.md) |
| Spotify app or Spotify Connect alone | Not yet supported; Spotify playback is not automatically exposed through UPnP AVTransport |

A compatible Samsung Frame and a computer running the bridge are still needed. The computer handles artwork, not music playback. MinimServer and local library access are needed only for the local-library features, not for Qobuz-only playback.

### Adding other music services

The bridge supports the default UPnP input and an experimental Roon input feeding the existing artwork/TV pipeline. Additional services need their own playback-source adapters. Each adapter should supply playback state, stable provider/album/track identifiers, title, artist, cover location and a source link. Provider-specific IDs must remain separate from Qobuz IDs; missing classical roles or recording dates must remain unknown. UPnP should remain the default so existing installations retain their behavior.

Spotify's [currently-playing API](https://developer.spotify.com/documentation/web-api/reference/get-the-users-currently-playing-track) offers an account-level input independent of JPLAY, MinimServer and a UPnP renderer. A proposed adapter would use user authorization with the read-only `user-read-currently-playing` scope and token refresh; it would not control audio. This adapter is **not implemented or live-tested**.

Spotify needs a distribution/design decision before it can be presented as supported:

- As checked October 10, 2026, new development apps are limited to five authorized users and the app owner must have Premium. Each user's own developer app is a DIY setup, not a frictionless public login. See [Spotify's development-mode guide](https://developer.spotify.com/documentation/web-api/tutorials/february-2026-migration-guide).
- Spotify requires attribution and links back, and restricts standalone metadata/artwork products. Its visual-content rules also need review for the Frame display and rotating history collage. Existing Qobuz layouts and history behavior should not simply be reused with an assumption of Spotify approval. See the [developer policy](https://developer.spotify.com/policy) and [display guidelines](https://developer.spotify.com/documentation/design).
- A Spotify account and a representative playback device are needed for end-to-end validation. Mocked API tests alone would not establish Spotify Connect or TV compatibility.

## On the screen

These are the images rendered by Listening Room Frame for the TV: album artwork, a detailed classical view, and the recent-album collage.

### Now playing

![Now-playing display with Frank Sinatra album artwork, artist and recording date](docs/images/now-playing.jpg)

### Classical recordings

Composer, work, movement and performers appear separately, with composition and recording dates clearly distinguished.

![Classical display with John Lewis Bach artwork and composer, movement, performer and date information](docs/images/classical.jpg)

### Your recent listening

A rotating collage brings the 28 most recent albums together.

![Seven-by-four collage of recent album artwork](docs/images/recent-albums.jpg)

Album artwork remains the property of its respective rights holders and is shown here to illustrate the display.

## Features

- Album cover, artist, title, and recording year where available.
- Classical cards with separate composer, work, movement, conductor, ensemble, soloist, and composition date.
- Explicit labels distinguish recording dates from original-release or release-date fallbacks. Uncertainty qualifiers remain visible.
- Persistent, deduplicated 28-album history. Replaying an album moves it to the front. A 7-by-4 collage appears every two minutes for 15 seconds.
- Local artwork through MinimServer; Qobuz artwork and track details through public album pages.
- Read-only file tags plus a persistent, per-track enrichment store and protected overrides.
- Respects normal TV mode and manual artwork choices. Restores the previous art after two minutes stopped or paused, or on a normal service stop.
- Reuses current/collage images and removes superseded images owned by this application.

## Status and compatibility

This is an early DIY release extracted from a working listening-room installation. It has been used with a Samsung QN65LS03AAFXZA, a CM5 Diretta UPnP renderer, MinimServer, and a Fedora NUC. The public package has automated tests; its generalized configuration has not been tested on another complete installation.

You need Python 3.10+, a compatible Samsung Frame reachable on your local network, and a renderer exposing UPnP AVTransport, a Sonos speaker with the optional SoCo dependency, or a Roon Server with the optional Node.js extension. Local metadata extraction additionally needs `ffprobe` from FFmpeg and access to the library files. Linux/systemd is the documented deployment target. A NUC is not inherently required, but other hardware and operating systems have not been validated.

Known limitations:

- Sonos has a room discovery command; a setup wizard and discovery for other inputs are not included. TV, renderer and MinimServer endpoints must be configured manually; static DHCP leases are helpful.
- Renderer service paths and metadata vary. The default AVTransport path matches the tested renderer. Use `renderer_control_url` for a different full control URL from the device description.
- MinimServer's `0$items` browse support and file URL mapping are assumed. Set `library_resource_prefix` to the exact URL path prefix preceding library-relative filenames.
- Qobuz integration parses public pages, not an authenticated catalogue API. Markup changes, region restrictions and missing credits can interrupt enrichment. Qobuz recording/composition dates are not comprehensively available.
- Multidisc history grouping depends on consistent album titles and recognizable disc directories; it is not universal release identification.
- Samsung firmware and Art Mode APIs vary. Switching uses the TV's normal image selection; there is no programmed crossfade. The bridge checks Art Mode directly because the TV can report standby while displaying artwork. It does not wake the TV or switch it into Art Mode.
- Missing artwork restores the previous TV art. There is no automatic artist-portrait search.
- Dates and other supplied metadata require source review. A populated field does not prove correctness.

## Set up in your browser

The optional [local setup page](SETUP.md) guides you through five setup steps, then opens a listening console with the actual generated TV image, signal indicators and a browsable record-sleeve rack. It distinguishes prepared images from timestamped TV selection confirmations. It can discover TVs and Sonos rooms, save your choices, check the connection, show a test image, start/stop the display and enable Linux user-service startup. It also provides a [local API](SETUP.md#local-api-for-tools-and-assistants) for assistants. Sonos has the guided setup path; Roon extension pairing and advanced UPnP configuration still need their source-specific steps. Installation still requires a terminal; an AI subscription is not required.

## Set up or troubleshoot with Codex

Download/clone this repository and open its folder as a local project in Codex. Run the task on the artwork computer, or give it ordinary SSH access to that computer with verified host keys. A cloud-only task cannot discover devices on your home network. See the [official local-project instructions](https://learn.chatgpt.com/docs/projects#use-local-projects-for-folders-and-codebases) for attaching the folder and checking the task's access.

Paste this setup request, replacing the bracketed details:

> Set up Listening Room Frame from this repository on [this computer / my artwork host]. Read README.md, SETUP.md and the guide for [Sonos / Roon / UPnP]. Inspect existing artwork services first and preserve their configuration and original-art state. Install the project dependencies in a virtual environment, start the local setup interface, discover devices, and let me choose the TV and room. Verify the TV model and help me approve its connection on the TV. Save private settings outside Git. Show a reversible test image, verify artwork and track changes, and configure automatic startup on a supported host. Explain any manual steps and distinguish tested behavior from assumptions. Do not change music playback, router settings or audio services, and do not obtain administrator credentials for me.

For configuration changes:

> Use the local setup UI/API described in SETUP.md to change the selected room/source to [my choice]. Inspect the saved configuration and running service first. Stop the artwork worker before saving changes; preserve restoration state and keep the original configuration recoverable. Verify the new source, then resume the display. Read any API token locally without printing or pasting it into chat. Do not run two artwork services against the same TV.

For troubleshooting:

> Diagnose why Listening Room Frame [describe the symptom]. Read SETUP.md and inspect the saved source, service status, latest API operation result, playback timestamp, private worker log and TV connection. Determine whether discovery, source metadata, artwork retrieval, TV authorization or display selection is failing. Start with read-only checks, keep audio playback untouched, and do not delete tokens or restoration state. Redact credentials, signed URLs, device identifiers and listening history from anything shared publicly. Make a minimal fix if needed, run the relevant tests and report what was verified versus what still requires my observation.

Use a locally running task with the access needed for the job; granting filesystem access does not automatically grant network or SSH access. You may need to approve network access, accept the TV prompt yourself, or personally perform an administrator-only host setup step. Do not paste passwords, TV tokens or the setup API token into the conversation. Existing `config.json` belongs to the direct bridge; the browser manager keeps its separate config under `~/.local/state/listening-room-setup`. Codex should follow the documented API rather than guess endpoints. Other local coding assistants can use the same repository guides and API.

## Install

Install Python using your operating system's package manager. Install FFmpeg only if you want local audio-file metadata extraction, then:

```sh
git clone https://github.com/kstirman/listening-room-frame.git "$HOME/listening-room-frame"
cd "$HOME/listening-room-frame"
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
cp config.example.json config.json
```

For Roon, use [the Roon setup guide](roon/README.md) and `config.roon.example.json` instead. It needs no renderer or MinimServer settings. Existing configurations default to UPnP.

Edit `config.json` for UPnP:

- `tv`: Frame hostname or IP. `tv_model`: exact model identifier expected by the safety check.
- `renderer`: renderer HTTP base URL. Optionally set `renderer_control_url`.
- For local-library playback, `minim_control`: actual MinimServer ContentDirectory control URL, including its server UUID.
- `local_art_hosts`: allowed local hosts serving playback resources and covers; list hostnames/IPs without schemes or ports.
- `library_root`: filesystem location of your music, as seen by this process.
- `library_resource_prefix`: matching MinimServer resource URL path prefix. The example defaults to `/minimserver/*/AudirvanaLibrary/`.
- Timing/history settings: seconds and number of distinct albums. Keep `history_limit` at 28 for the intended layout.

With music playing and the Frame in Art Mode, run in the foreground:

```sh
.venv/bin/python bridge.py --config config.json --state-dir "$HOME/.local/state/listening-room-frame"
```

Accept the TV's connection prompt if shown. Confirm the correct cover appears. Stop with Ctrl-C and confirm your previous art returns. The service never powers on the TV, switches its input, or controls audio playback.

### Run automatically on Linux

The supplied unit expects the checkout and virtual environment at `~/listening-room-frame`:

```sh
mkdir -p "$HOME/.config/systemd/user"
cp frame-art.service "$HOME/.config/systemd/user/"
systemctl --user daemon-reload
systemctl --user enable --now frame-art.service
journalctl --user -u frame-art.service -n 30
```

For operation without an interactive login, enable user lingering according to your distribution's instructions. Reboot and verify service startup and actual TV behavior on your installation.

Stop with `systemctl --user stop frame-art.service`. Keep the state directory: it contains the original art ID needed for restoration. If the TV was unreachable at shutdown, restore later with:

```sh
.venv/bin/python bridge.py --config config.json --state-dir "$HOME/.local/state/listening-room-frame" --restore
```

## Metadata cleanup tools

These tools support a reviewed cleanup workflow; they do not automatically research and correctly identify every recording.

1. Inventory tags and embedded/folder artwork without changing audio:

```sh
.venv/bin/python library_enrich.py --library-root /path/to/music
```

This writes `inventory.json` and extracted cover copies beneath `/path/to/music/.listening-room-metadata/`. It does not publish covers into MinimServer or change your audio tags. Extracted cover storage is not automatically pruned.

2. Report missing fields, including classical tracks identified through overrides:

```sh
.venv/bin/python metadata_tools.py --library-root /path/to/music audit > metadata-audit.json
```

Missing conductor, ensemble or movement fields may be inapplicable; review the candidates. Composition dates describe the work/version, not the recording session. Identify recording editions before borrowing metadata from another catalogue.

3. Research gaps and write a proposal, with provenance for each field:

```json
{
  "tracks": {
    "Artist/Album/01.flac": {
      "fields": {"recordingDate": "1959", "compositionDate": "c. 1783"},
      "provenance": {
        "recordingDate": "Original booklet, page 4: recording sessions in 1959",
        "compositionDate": "Publisher catalogue URL and version identification"
      }
    }
  }
}
```

4. Apply reviewed additions:

```sh
.venv/bin/python metadata_tools.py --library-root /path/to/music apply proposal.json
```

The tool fills empty fields only, preserves populated enrichment fields and `track-overrides.json`, backs up an existing database, and atomically replaces `enriched.json`. It never edits audio files. Run one metadata writer at a time. Restart the display service after editing metadata to clear its track-detail cache.

Local enrichment applies only to matching local file paths; it does not automatically transfer to Qobuz editions. `recording-dates.json` and `work-dates.json` are empty extension registries. The date tests illustrate their schemas using public music facts.

Making art available to JPLAY is a separate step: install a reviewed cover sidecar using the naming rules for your MinimServer configuration, rescan MinimServer, then refresh JPLAY's library. This public release deliberately excludes the original installation's album-specific publishing scripts.

## Storage and privacy

TV state, authorization token (if issued), current images and recent covers live in the selected state directory. Recent cover history is capped and superseded application-owned TV images are cleaned up. Inventory/extracted covers and metadata backups are separate and are not size-capped.

Keep `config.json`, state, tokens, library inventories, enrichment databases, downloaded artwork/booklets and private research out of Git. The repository contains no music files. README screenshots include album artwork to illustrate the display; operational artwork caches are excluded. Album/track IDs are used for public Qobuz lookups; signed playback URLs are not submitted to Qobuz by this application. The app reads local audio tags, not audio playback data.

## Tests

```sh
.venv/bin/python -m unittest discover -v
```

Tests use fake TV/network objects and temporary metadata fixtures. They cover restoration, manual selections, collage timing and uniqueness, local resource matching, date semantics, and preservation of reviewed metadata. Passing tests do not certify another TV model or firmware.

## Project layout

- `bridge.py`: playback watcher, artwork resolution, TV lifecycle and collage scheduling.
- `layouts.py`: 4K album, classical and collage images.
- `classical.py`, `dates.py`, `library_metadata.py`: metadata interpretation and local enrichment.
- `library_enrich.py`, `metadata_tools.py`: inventory, field audit and reviewed additions.
- `frame-art.service`: optional systemd user service.

Independent community project; not affiliated with Samsung, Qobuz, MinimServer or JPLAY. See `LICENSE` for the application license. Third-party libraries, media and metadata retain their own terms.

### Recovery from stalled TV requests

The Linux service bounds each complete artwork update to 120 seconds, including TV cleanup. A stalled operation logs only stack locations (no source text or local values) and exits unsuccessfully so the supplied service restarts it after 30 seconds. Idle restoration on exit and `--restore` have a 30-second limit. A stop request shortens an active deadline to at most 30 seconds. Standalone runs exit on timeout and need to be started again manually.

This also covers TV event streams that keep a socket active without delivering the requested response. Recovery uses the existing saved artwork state; an upload interrupted before its returned ID is saved may leave an orphan image on the TV. The deadline is a recovery safeguard, not proof of the cause of every display freeze.

Transient update failures retry after five seconds so an old album does not remain displayed for a full minute after a brief connection error. Each retry polls playback again. Error diagnostics identify the operation phase; playback changes log hashed track/album identifiers and transport state, never signed stream URLs or raw metadata. These distinguish changing renderer metadata from a stalled display without exposing source credentials.


### Track duration and composition context

Cards show the current track's total length when supplied by UPnP, Sonos,
Roon, a Qobuz album page, or local file metadata. Unknown/live-stream lengths
stay blank. There is no elapsed-time animation or extra per-second TV update.

For Qobuz through UPnP, an ordered, fully timed work group adds a static
movement strip: segment widths reflect durations, the current movement is
highlighted, and a numbered legend gives each length. This works across albums;
there are no album-specific rules. Grouping requires a shared work heading or
explicit work-and-numbered-movement titles, consecutive numbering from one,
and lengths for every listed movement. Adjacent classical tracks sharing an exact
`Work: section` prefix and composer can also form unnumbered parts, labeled
“Part X of Y.” Standalone colon titles, pop titles, duplicate sections and gaps
in explicitly numbered movements do not qualify. Repeated headings remain separate.
Incomplete or ambiguous groups show duration alone. Numbered movement groups
are limited to16; explicitly named part groups can contain up to128 tracks.
The strip describes the album's listed work sequence, not shuffle/queue order.

Sonos and Roon currently provide duration only: their now-playing adapters do
not supply a complete composition catalog. Local files with reviewed work and
composer entries in `.listening-room-metadata/enriched.json` can show composition
context too. Only matching files are probed (bounded and cached), then separated
by album/recording artist and ordered by disc and track tags. Legacy album names
ending in a disc number are supported. Missing lengths, duplicate track positions,
numbering gaps or ambiguous recordings suppress the strip. Numbered movements use
“Movement”; unnumbered opera tracks use “Part.” Long works retain proportional
segments and a whole-work duration rather than an unreadably long duration legend.
This is whole-work context, not act/scene inference; missing acts are not guessed.
Roon users must update/restart the Node extension as well as the Python bridge
for lengths to appear. Older extension snapshots remain compatible.

Metadata references: [Roon transport length](https://roonlabs.github.io/node-roon-api/other_node-roon-api-transport_lib.js.html),
[SoCo current-track fields](https://soco.readthedocs.io/en/latest/api/soco.core.html#soco.core.SoCo.get_current_track_info).
