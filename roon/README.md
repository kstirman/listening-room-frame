# Roon input (experimental)

Follow one Roon output on the Samsung Frame. JPLAY, UPnP, MinimServer and a local music library are not needed. Requires a Roon Server, a compatible Frame, Python dependencies from the main project, and Node.js 20+ with pnpm. The Node extension and Python bridge run on the **same computer and user account**, sharing a private directory. The extension subscribes to transport metadata and fetches images; it never issues playback or volume commands.

**Validation:** automated simulated zone/image tests, Python bridge regression tests and an official-library initialization smoke test. No real Roon Server pairing, live Roon playback, or Roon-to-TV installation has been verified. Treat this as experimental until the checklist below passes on your installation.

## Install and pair

From the project checkout:

```sh
cd roon
pnpm install --frozen-lockfile --ignore-scripts
node app.js --state-dir "$HOME/.local/state/listening-room-roon" --email 'you@example.com'
```

The Roon registration API requires an email string; supply your own extension contact address. It is sent to your Roon Server as extension information, not committed to this repository. Official Roon dependencies are pinned to commits, with transitive dependencies in `pnpm-lock.yaml`. They include the upstream deprecated `node-uuid` dependency.

In Roon, open **Settings → Extensions**, enable **Listening Room Frame**, and pair it with the intended server. Discovery requires network reachability to that server. Pairing credentials are stored in `config.json` inside the private state directory, not the project configuration.

In another terminal, inspect the `outputs` list:

```sh
python3 -m json.tool "$HOME/.local/state/listening-room-roon/now-playing.json"
```

Choose the `id` for your listening output, stop the extension with Ctrl-C, then restart it with that ID:

```sh
node app.js --state-dir "$HOME/.local/state/listening-room-roon" --email 'you@example.com' --output-id 'YOUR-OUTPUT-ID'
```

No output is selected automatically. The extension follows the zone containing that output, including after regrouping. If the output disappears or selection is ambiguous, it reports stopped instead of switching rooms. Replacing a device can require choosing a new output ID.

## Start the display

From the project root, copy `config.roon.example.json` to a private configuration file and set the Frame hostname/model. Keep `roon_state_dir` equal to the extension's state directory. Use a separate display state directory from an existing UPnP installation:

```sh
cp -n config.roon.example.json config.json
.venv/bin/python bridge.py --config config.json --state-dir "$HOME/.local/state/listening-room-frame-roon"
```

Do not overwrite an existing configuration; choose another filename if one already exists. Do not run two display bridges against the same TV simultaneously. Roon mode bypasses all UPnP, Qobuz and local-file lookups. Omitting `source` retains the existing UPnP behavior.

For Linux startup, the existing `frame-art.service` can run the display with your Roon config/state paths. Create a separate user service for the extension using the **absolute path** to your installed Node binary, `Restart=on-failure` and `UMask=0077`; its command is the one above. The display tolerates either startup order. Do not enable startup until foreground validation succeeds.

## Behavior and limits

- Displays Roon's three-line text (or its available two-/one-line fallback) and artwork. Text is preserved, not interpreted as authoritative composer, work, movement or recording-date fields. Long text can be truncated to fit the card.
- Track text changes refresh the card within an album. History identity uses server identity, image key and the third display line; Roon transport does not supply a canonical album ID. Changing image keys or display conventions can split history entries.
- Pause/stop and loss of the selected output use the configured idle grace before restoring original TV artwork. A stopped/crashed extension becomes stale after 30 seconds; the idle grace then applies. Missing covers restore original artwork rather than leaving a misleading card.
- Image requests retry after failure, time out logically after 15 seconds, and discard responses from an old track/core. The extension retains one staging image; the display maintains its own bounded history copies.
- The extension writes a heartbeat every five seconds. Keep both processes on the same host with a working system clock. Run only one extension process per state directory.
- Pairing state, snapshots and covers are private. Do not upload the state directory or logs. Revoke the extension in Roon and stop both processes to disconnect it; restore the original TV art before removing display state.

## First real-installation checklist

1. Pair the intended server; verify available output names and IDs.
2. Start a track on the chosen output; verify title, artist/album text and art on the Frame.
3. Change tracks within an album, then change albums; verify movement text/card and collage return.
4. Pause/stop, remove/regroup the output, disconnect/reconnect Roon Server and restart the extension. Confirm stale artwork clears and new playback recovers.
5. Confirm no audio output, volume or transport state changes are caused by the extension.

Run automated tests without Roon:

```sh
node --test roon/test.js
.venv/bin/python -m unittest discover
```

API references: [Roon extension API](https://github.com/RoonLabs/node-roon-api), [transport zone fields](https://roonlabs.github.io/node-roon-api-transport/Zone.html), [image service](https://github.com/RoonLabs/node-roon-api-image).
