# Browser setup and local API

The setup page runs on the computer that generates the artwork. It is an optional alternative to editing JSON and starting `bridge.py` directly. Linux is the deployment target; foreground setup also works on macOS. Windows is not supported by this manager. Sonos has a complete discovery/selection flow. Roon still requires the separate extension setup, and UPnP uses manually entered renderer settings.

## First installation

Install Git and Python 3.10+ using your operating system's normal tools. On the artwork computer:

```sh
git clone https://github.com/kstirman/listening-room-frame.git "$HOME/listening-room-frame"
cd "$HOME/listening-room-frame"
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-sonos.txt
.venv/bin/python setup_server.py
```

Open **http://127.0.0.1:8765** in a browser on that computer. No music-service login or AI subscription is needed. This is still a command-line installation; there is no packaged installer yet.

1. Put the Frame in Art Mode. Choose **Find my devices**. Discovery can take several seconds. It does not control music or the TV. If the TV is missing, enter its local address and choose **Check address**; the model field fills automatically.
2. Select a playback system and room. Roon requires its already-configured extension state folder; UPnP requires the actual AVTransport control URL. See the source-specific guides.
3. **Save my choices**, then **Check connection**. Accept the TV's connection prompt if shown. Retry after approval. Connection checks read the playback source and Art Mode; they do not show an image or turn on the TV.
4. **Show the test image** displays a generated setup card for 15 seconds and attempts to restore the original art. It does not require playing music. Manual TV selections are preserved. If the TV becomes unreachable, use **Restore original artwork** after reconnecting.
5. Play music and choose **Start display**. Verify track and album changes. **Stop display** attempts restoration and disables playback following until you start it again.
6. On Linux, **Enable automatic startup** installs and enables a systemd user service for future user sessions. It does not launch a second manager while this foreground one is running. Keep the process running today, or exit it and run `systemctl --user start listening-room-setup.service`. For unattended boot, configure user lingering using your distribution's instructions; this UI does not request administrator access. Verify service startup and real display behavior after reboot.

The browser can close while the setup process stays running. Closing the terminal/process stops its managed display and attempts restoration. Automatic startup is not implemented for macOS. Only one configured source is followed; automatic source switching is not implemented.

## Your listening room and the image preview

New installations open a five-step setup guide: find the Frame, choose a room, check the TV connection/test image, preview music, and configure startup. Saved installations open Now Playing. Use **Connections** to return to setup. Sonos has room discovery; Roon still needs its external extension and UPnP needs its control URL.

Now Playing shows the **actual generated image**, including the collage when selected, plus track information and up to eight recent covers. The console uses a dark faceplate, warm indicators and a sleeve rack. Select a sleeve for a larger artwork-only view; this does not control music or the TV. The music indicator lights only for fresh playing metadata while the display worker is running. The TV indicator reflects the timestamped selection confirmation below, not continuous TV monitoring. The empty record-sleeve illustration is a placeholder, not music metadata or a claim about the TV. Reduced-motion preferences disable transitions.

**Preview current track** reads the selected source, fetches its art and renders a card without connecting to or changing the TV. It requires a playing track with supported art. When the display is running, its worker updates the preview as it renders/selects images. Preview-only images and history live separately from the display's restoration state.

- **Prepared** means the application rendered those exact image bytes. It does not mean the TV received them.
- **TV selection confirmed at…** means the TV returned the expected content ID after selection at that time. It is not a camera feed or continuous proof of the physical screen.
- **App control released** means the bridge released its owned artwork state during restoration; the last generated preview stays visible for reference. A manual TV selection may have been preserved.
- **Waiting** means the current image has not been confirmed selected. The TV may need approval, Art Mode or connectivity; check the operation result.

The browser fetches private JPEGs with its API token, without putting credentials in image URLs. It checks the image revision before replacing the preview so a concurrent update is not shown with the wrong revision. Recent covers contain your private listening/preview history and are not uploaded anywhere.

## Existing installations and a headless host

Do not run the original `frame-art.service` and the setup manager against the same TV at once. The manager refuses TV operations if it detects that active user service on Linux; independently named or remote artwork processes cannot be detected. Stop those separately before testing. The manager does not alter their configuration or service files.

The manager saves its own configuration and display state in `~/.local/state/listening-room-setup`, not the repository's `config.json`. It keeps the previous saved settings in `config.previous.json` and does not import an existing configuration automatically. Changing TVs preserves the previous TV token as `display/token.previous` and requires authorization for the new TV. For a rollback: stop the managed display, stop/disable `listening-room-setup.service` if installed, then restart the original artwork service. Keep both state directories for restoration; do not delete TV tokens or state as a troubleshooting shortcut.

For a headless computer, start the manager there and tunnel its loopback port from the computer with your browser:

```sh
ssh -L 8765:127.0.0.1:8765 YOUR_USER@YOUR_ARTWORK_HOST
```

Then open http://127.0.0.1:8765 locally. Use verified SSH host keys. The tunnel must use the same local port as the server (default 8765). The manager deliberately binds only to loopback; do not expose it through router port forwarding or a public proxy. It has no remote-login UI.

## Local API for tools and assistants

The browser and agents use the same API. It is available only while `setup_server.py` is running. All endpoints except browser-session bootstrap require:

```
Authorization: Bearer <contents of the private api-token file>
```

The token is generated in the manager's state directory with owner-only permissions. Read it locally in your client; never paste it into chat, URLs, logs, or Git. A same-origin browser gets it from `GET /api/session`. Requests from other website origins are rejected, and no CORS access is offered. Device addresses and room identifiers are private settings, not secrets to publish.

| Request | Behavior |
| --- | --- |
| `GET /api/status` | Saved config, worker state, last operation result, source status/title, and startup state |
| `POST /api/discover` with `{}` | Read-only Sonos room and Samsung TV discovery |
| `POST /api/probe` with `{"host":"frame.local"}` | Read-only TV identity check |
| `POST /api/config` with source configuration | Validate and atomically save settings while stopped |
| `POST /api/preview` with `{}` | Render the current track locally without any TV request |
| `GET /api/image/current` | Authenticated JPEG; `X-Image-Revision` is its SHA-256 |
| `GET /api/cover/<64-hex-hash>.jpg` | Authenticated recent cover; filename comes from status |
| `POST /api/check` with `{}` | Read playback and attempt Art Mode connection/authorization |
| `POST /api/test` with `{}` | Show generated setup art for 15 seconds, then restore |
| `POST /api/start` with `{}` | Follow playback; remember this choice for manager restart |
| `POST /api/stop` with `{}` | Stop current operation/display and disable resume |
| `POST /api/restore` with `{}` | Retry restoring the original art while stopped |
| `POST /api/startup` with `{}` | Install/enable the Linux user service; no sudo |

Discovery, probe, connection, preview, test and restoration are asynchronous: poll `/api/status` until `busy` is false, then check `result.ok` and `result.data` or `result.message`. A successful POST means the operation started, not that it passed. `operation_id` distinguishes results. `presentation` provides the image revision, prepared/confirmed timestamps and status; `recent` provides cover filenames and labels. Confirmation is historical, not continuous TV surveillance. Starting the display is long-lived: `running` stays true; inspect `playback` for source health. `playback` includes a timestamp and sanitized error type; it does not prove physical TV appearance. `view`/`title` reflect saved display state, which may be stale if a request failed. Errors never include raw signed stream URLs. Private worker logs may contain album names from the bridge; inspect locally and redact before sharing.

Example Sonos config body (replace placeholders with discovered values):

```json
{
  "source": "sonos",
  "tv": "frame.local",
  "tv_model": "YOUR_EXACT_MODEL",
  "sonos_uid": "YOUR_DISCOVERED_ROOM_UID"
}
```

The API fixes polling at two seconds and idle restoration at 120 seconds. For UPnP, it accepts `renderer_control_url`, `local_art_hosts` and optional `minim_control`, `library_root`, `library_resource_prefix`. Roon accepts `roon_state_dir`; output selection still belongs to the Roon extension. The UI/API does not install dependencies, update software, change network settings or control music playback.

Samsung’s [network-status guide](https://www.samsung.com/ca/support/tv-audio-video/verify-network-status-on-your-samsung-tv/) illustrates finding the TV’s IP settings; menu names vary by model.

## Diagnostics and limits

- **Nothing discovered:** check local-network reachability, guest-network isolation and multicast restrictions; try the TV address manually. Sonos room discovery needs the optional SoCo dependency.
- **TV not connected:** check address/model, Art Mode and its approval prompt. The application does not power on the TV or switch modes.
- **Stopped/no title:** start music in the selected room. Line-in, TV audio, radio and some services can have incomplete metadata.
- **Wrong room:** stop the display, choose another room, save and start again.
- **Another service is running:** stop the old artwork service before starting this one; never stop music services for this purpose.
- **Source updates but art is stale:** inspect private `worker.log`, the saved display state and TV Art Mode. Preserve state; use Stop/Restore before retrying.
- **After a crash:** the manager retries a failed display worker after 30 seconds while enabled. Setup operations are bounded. An interrupted upload can leave an orphan TV image; interrupted restoration may need an explicit retry.

Discovery is best-effort IPv4 SSDP on the local network and probes at most 16 responding hosts for Samsung's API. The host must resolve to a private non-loopback address. VLANs, sleep, firmware, model compatibility and permissions can prevent discovery or Art Mode access. Startup without login and reboot persistence require testing on the target host. No claim of universal Frame, Sonos service, or Roon compatibility is made.

## Validation status

The regression suite covers the bridge plus configuration validation/backups, authenticated HTTP routes, cross-origin rejection, worker serialization, service conflict handling, restoration and timeout paths. Browser checks on macOS verified live Sonos discovery, manual Frame identity lookup, configuration saving/reload, source-specific fields and unsaved-change protection. The record-sleeve redesign adds tested exact-image publication, verified-selection/release labels, a TV-free preview action and authenticated image routes. The new test-image worker and Linux startup installer have automated tests; a live test-image cycle through this UI and Linux reboot/startup acceptance remain to be verified. Sonos-to-Frame Qobuz playback and track changes were verified separately before this interface was added.
