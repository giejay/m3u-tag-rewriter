# M3U Tag Rewriter

Downloads one or more M3U playlists from Dropbox, injects the `tvc-stream-timestamps="rewrite"` attribute into every `#EXTINF` line, and uploads the modified files back to Dropbox under new names. Runs on a configurable cron schedule inside Docker.

## What it does

For every playlist URL you configure, the script:

1. **Downloads** the M3U from a direct Dropbox link (no auth needed for download)
2. **Injects** `tvc-stream-timestamps="rewrite"` into each `#EXTINF` line, right before the `,Title` separator — e.g.:

   ```
   Before:
   #EXTINF:-1 tvg-id="someid.nl" group-title="Group",SomeChannel

   After:
   #EXTINF:-1 tvg-id="someid.nl" group-title="Group" tvc-stream-timestamps="rewrite",SomeChannel
   ```

3. **Uploads** the result to your Dropbox with a suffix appended to the original filename:
   ```
   MyProject.m3u  →  MyProject-include-tvstream-tag.m3u
   ```
4. **Prints the direct-download URL** of the uploaded file at the end of each run.

The injection is idempotent — if the tag is already present it won't be added again.

## Requirements

- Docker & Docker Compose
- A [Dropbox app](https://www.dropbox.com/developers/apps) with `files.content.write` and `sharing.write` permissions

## Setup

### 1. Create a Dropbox app

1. Go to <https://www.dropbox.com/developers/apps> → **Create app**
2. Choose **Scoped access** → **Full Dropbox**
3. Under **Permissions**, enable:
   - `files.content.read`
   - `files.content.write`
   - `sharing.write`
4. Copy the **App key** and **App secret**

### 2. Configure `.env`

Copy `.env.example` to `.env` and fill in your values:

```dotenv
DROPBOX_CLIENT_ID=your_app_key
DROPBOX_CLIENT_SECRET=your_app_secret

# Comma-separated list of direct-download M3U URLs (dl=1)
DROPBOX_PLAYLISTS=https://www.dropbox.com/.../foo.m3u?rlkey=...&dl=1,https://...

# Output suffix (optional, default: -include-tvstream-tag)
# DROPBOX_OUTPUT_SUFFIX=-include-tvstream-tag

# Cron schedule (default: 01:00 every night)
CRON_SCHEDULE=0 1 * * *

# Timezone for the cron schedule
# TZ=Europe/Amsterdam
```

### 3. Build and start

```bash
docker compose up -d --build
```

### 4. Authorise with Dropbox (first run only)

```bash
docker compose exec m3u-modifier python3 /app/m3u_dropbox_modifier.py
```

The script prints an authorisation URL. Open it in your browser, click **Allow**, then paste the code back into the terminal. The token is saved to a Docker volume and reused automatically on all future runs — no re-auth needed.

## Usage

### Run manually

```bash
docker compose exec m3u-modifier python3 /app/m3u_dropbox_modifier.py
```

### View logs

```bash
docker compose logs -f
```

### Change the schedule

Edit `CRON_SCHEDULE` in `.env` (standard 5-field cron expression) and restart:

```bash
docker compose up -d --build
```

### Change the output suffix

Set `DROPBOX_OUTPUT_SUFFIX` in `.env`, e.g.:

```dotenv
DROPBOX_OUTPUT_SUFFIX=-rewritten
```

`MyProject.m3u` → `MyProject-rewritten.m3u`

## Configuration reference

| Variable | Required | Default | Description |
|---|---|---|---|
| `DROPBOX_CLIENT_ID` | ✅ | — | Dropbox app key |
| `DROPBOX_CLIENT_SECRET` | ✅ | — | Dropbox app secret |
| `DROPBOX_PLAYLISTS` | ✅ | — | Comma-separated direct-download M3U URLs |
| `DROPBOX_OUTPUT_SUFFIX` | ❌ | `-include-tvstream-tag` | Suffix added before `.m3u` in the output filename |
| `DROPBOX_TOKEN_FILE` | ❌ | `/data/dropbox_token.json` | Path to the cached OAuth token |
| `CRON_SCHEDULE` | ❌ | `0 1 * * *` | Cron expression for the automatic run schedule |
| `TZ` | ❌ | `Europe/Amsterdam` | Timezone used for the cron schedule |

## Token storage

The OAuth token is stored in a named Docker volume (`token-data`) and persists across container restarts and rebuilds. The token includes a refresh token, so it never expires unless you revoke the app's access in your [Dropbox connected apps](https://www.dropbox.com/account/connected_apps).
