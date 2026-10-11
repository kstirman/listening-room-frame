# Sonos input (experimental)

The bridge follows one explicitly selected Sonos room on the local network. It reads the group's coordinator, so the selected room can join another group without displaying its old standalone track. It never sends play, pause, volume, queue, or grouping commands. JPLAY, MinimServer, Roon, and music-service developer credentials are not required.

## Setup

Use Python 3.10+ and the virtual environment from the main README:

```sh
.venv/bin/python -m pip install -r requirements-sonos.txt
.venv/bin/python sonos_source.py --discover
cp -n config.sonos.example.json config.json
```

Set `sonos_uid` to the chosen room's discovered UID and `tv` to the Frame hostname/address. The UID survives a room rename; replacing a speaker requires a new selection. Discovery output contains private device identifiers: do not commit it. A stereo pair should use its primary room, not a hidden satellite.

Run on the same reachable local network; multicast discovery across VLANs is not guaranteed. No matching speaker means stopped, never an automatic switch to another room. Discovery retries at most every 30 seconds when disconnected. Normal metadata polling defaults to two seconds, with four-second Sonos request timeouts.

Stop any existing artwork bridge targeting the same TV before starting this one. Use a separate state directory, and retain the existing configuration for rollback:

```sh
.venv/bin/python bridge.py --config config.json --state-dir "$HOME/.local/state/listening-room-sonos"
```

The usual Frame authorization, Art Mode, manual-selection and restoration behavior applies. On stop, pause, or disconnection, original art is restored after `idle_seconds` (120 by default); missing artwork restores it immediately. Normal exit also restores art. To roll back, stop this bridge normally and restart your previous artwork service.

## Metadata and service limits

Title, artist and album come from Sonos; the display does not invent classical composer, conductor or movement fields. Movement information appears when the supplied title contains it. Artwork is fetched from the URL supplied by the trusted speaker, including external service hosts; HTTP(S), redirects and download size are bounded. Stream URLs are hashed for change detection, never downloaded as audio or logged.

Sonos playback from services such as Apple Music or Spotify may expose useful metadata, but each service/input needs testing. This is not general access to what is playing on an iPhone, HomePod or arbitrary Spotify Connect device. AirPlay, radio, TV and line-in metadata/artwork may be absent or incomplete. Music and artwork rights and service terms still apply; this adapter is not service approval for artwork redistribution.

History groups by album, artist and artwork URL, an approximate identity: compilations or changing service artwork URLs may create extra entries. Metadata and cached covers stay in the private state directory. Sonos library support comes from [SoCo](https://github.com/SoCo/SoCo), pinned in the optional requirements file; default UPnP/Roon installs do not import SoCo.

## Validation

Automated tests cover explicit room selection, group-coordinator changes, same-album track refresh, missing artwork, radio-art fallback, disconnection/reconnection, download limits, unsafe URL rejection, and bridge-to-fake-TV updates/restoration. Local discovery and read-only stopped-state queries have been verified on real speakers. Live music metadata, regrouping during playback, service-specific artwork and Sonos-to-Frame behavior remain unverified.

For a first live test: play two tracks from one album, change albums, pause longer than the idle timeout, then resume. Check the title and artwork on the Frame at every step. Separately test grouping and disconnect/reconnect. Report the Sonos input/service used; success with one service does not certify others.
