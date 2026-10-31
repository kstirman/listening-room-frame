# Spotify current-track display

The experimental Spotify source follows the authorized account's current music track, including playback on Spotify Connect devices. Audio continues in Spotify. This app only reads what is playing and sends artwork to the Frame; it cannot start, pause, transfer, skip or change volume.

The current sleeve occupies almost the full height of the 4K image, without cropping or stretching. Track, artist, album, release date and duration appear beside it with Spotify's official logo and a track link. The listening console provides the clickable link and full supplied metadata. Release dates are not recording dates. Spotify metadata is not sent to the Qobuz matcher or used to infer classical roles or influences.

This integration has automated adapter, authorization and display tests. A real authorized account and Frame are still required for end-to-end validation. One source is selected at a time; choosing Spotify does not automatically switch between Spotify and UPnP.

## Connect an account

Use your existing Spotify login; a separate developer login is not needed. This personal integration does require Spotify Premium and a one-time app registration. A normal Free account alone cannot enable this API connection.

1. In the [Spotify Developer Dashboard](https://developer.spotify.com/dashboard), create or use your own app and add `http://127.0.0.1:8888/callback` as its Redirect URI. Use this exact numeric loopback address, not `localhost`.
2. Copy the app's **client ID**. No client secret is needed. As checked October 31, 2026, Development Mode requires the app owner to have Spotify Premium and new apps allow up to five authorized users. Add the listening account to the app's user allowlist if it is not the owner.
3. Install this repository's dependencies in its virtual environment, then run on the computer where the browser will open:

   ```sh
   .venv/bin/python spotify_auth.py --client-id YOUR_CLIENT_ID
   ```

4. Sign in directly with Spotify and grant `user-read-currently-playing`. The callback waits up to five minutes. The terminal confirms success; the browser's “authorization received” message alone does not confirm token storage.
5. Tokens are saved with owner-only permissions to `~/.local/state/listening-room-spotify/spotify-tokens.json`. Keep that file private. Do not paste its contents into chat or a Git repository.

For a headless artwork host, run authorization there with `--no-browser` and first forward its callback port from the browser computer using ordinary saved-key SSH:

```sh
ssh -N -L 8888:127.0.0.1:8888 YOUR_ARTWORK_HOST
```

Open the printed authorization URL in that computer's browser. Keep the SSH tunnel open until the remote terminal confirms success. Only one process may use the callback port. Alternatively authorize locally and securely transfer the private token file to your own artwork host with owner-only permissions. `--token-file` and `--port` can customize these locations; the registered redirect must match the chosen port.

## Select Spotify

In the local setup page, choose **Spotify** under Connections and enter the token file's absolute path on the artwork host. Save, play a song in Spotify, and use **Preview current track** before starting the display. The setup page stores the path, never the token contents. Check that the displayed track and cover match Spotify and that the link opens the correct track.

For the direct service, copy `config.spotify.example.json` to your private `config.json`, set the verified TV address/model and token path, and use a separate Spotify display state directory:

```sh
.venv/bin/python bridge.py --config config.json --state-dir "$HOME/.local/state/frame-spotify"
```

Stop any other artwork worker targeting the same TV before starting this one. The existing UPnP configuration can be restored to return to that source. Do not replace an existing customized production bridge without reviewing its differences and preserving a rollback copy.

## Behavior and limits

- Polls at most once every five seconds per adapter; respects HTTP 429 `Retry-After`. It does not repeatedly fetch a cover as tracks change within the same album.
- Refreshes expiring access tokens and preserves rotated refresh tokens. A revoked or expired grant requires sign-in again. Spotify currently documents a six-month refresh-token lifetime for Dashboard apps; automatic refresh does not extend it.
- Paused or absent playback uses the normal idle grace. Connection failures may retain a known response for at most 30 seconds before marking the source stopped. Unsupported items, ads, podcasts and Spotify local files do not generate album cards; no track is guessed.
- Follows the account, not a fixed room or device. Music played elsewhere on the same account can change this display.
- Uses Spotify-supplied cover art from its image CDN. A current cover/display image is cached locally; Spotify covers are excluded from the recent-album collage, record rack and permanent cross-service catalog. Use a separate state directory for the direct bridge when changing sources. Removing authorization does not itself delete files already on the local disk or TV; stop the display to restore art, then remove its private state if desired.
- Missing metadata remains missing. This is not a Spotify recommendation, influence or full-credit API.
- An account that gets HTTP 403 must check the app owner's Premium status, allowlisted users and Spotify's access restrictions. Sign-in success does not prove API eligibility.

## Spotify sources and attribution

Implementation references: [currently playing](https://developer.spotify.com/documentation/web-api/reference/get-the-users-currently-playing-track), [PKCE](https://developer.spotify.com/documentation/web-api/tutorials/code-pkce-flow), [redirect requirements](https://developer.spotify.com/documentation/web-api/concepts/redirect_uri), [token refresh](https://developer.spotify.com/documentation/web-api/tutorials/refreshing-tokens), [rate limits](https://developer.spotify.com/documentation/web-api/concepts/rate-limits), and [Development Mode requirements](https://developer.spotify.com/documentation/web-api/tutorials/february-2026-migration-guide).

Spotify's [design guidelines](https://developer.spotify.com/documentation/design) require its branding, links and preservation of artwork. Its [developer policy](https://developer.spotify.com/policy) also restricts standalone metadata/artwork products and audiovisual uses. This experimental personal current-playback companion is not an approval from Spotify; broader distribution requires reviewing the applicable terms. Spotify remains the playback application. Do not market this as a licensed Spotify hardware integration.
