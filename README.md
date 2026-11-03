# wallabag-podcast

Turn your Wallabag saved articles into podcasts. Create multiple randomly-named podcasts, queue random unread articles, generate MP3s with [Kokoro-FastAPI](https://github.com/remsky/Kokoro-FastAPI), and subscribe via per-podcast RSS feeds. Articles are never repeated across podcasts; Wallabag state is never modified.

## Quick Start

### Local dev
```bash
uv sync
cp .env.example .env  # fill in Wallabag credentials
# Start TTS in another terminal
docker run -p 8880:8880 ghcr.io/remsky/kokoro-fastapi-cpu:latest
# Run app
mise run start  # or: uv run uvicorn app.main:app --reload
```
Open http://127.0.0.1:8000. First start creates a podcast automatically. Test Wallabag in **Settings** → **Test Connection**.

### Docker
```bash
cp .env.example .env
docker compose up -d
```
Opens at http://localhost:8000. Both app and Kokoro run in compose; audio/DB persist in `./data`. Set `BASE_URL` to your LAN IP for phone access.

## Configuration

Key variables in `.env`:
| Variable | Description |
| --- | --- |
| `WALLABAG_URL`, `WALLABAG_CLIENT_ID`, `WALLABAG_CLIENT_SECRET`, `WALLABAG_USERNAME`, `WALLABAG_PASSWORD` | Wallabag OAuth credentials (never stored in DB/UI). |
| `KOKORO_BASE_URL`, `KOKORO_DEFAULT_VOICE`, `KOKORO_SPEED` | TTS settings (default voice e.g. `af_heart`). |
| `BASE_URL` | Feed/audio base URL for podcast apps (use LAN IP for phones). |
| `HOST`/`PORT` | Bind address/port (`0.0.0.0` to expose on LAN). |
| `DATA_DIR` | DB and audio location (default `./data`). |
| `EXCLUDE_TAGS`, `MIN_TEXT_CHARS`, `MAX_FETCH_PAGES` | Filtering options. |

## Usage

1. **Queue**: On a podcast page, **Add N Random Articles** (configurable in Settings) or paste a Wallabag URL/ID to add a specific article. Articles already queued/generated or in other podcasts aren't offered again.
2. **Review**: **Delete** removes from queue (and deletes MP3 if generated; article stays unread). **Archive** marks article read in Wallabag but keeps episode in feed.
3. **Generate**: **Generate Audio** synthesizes queued articles. Failed/cancelled episodes are retried automatically. Use **Stop Generating** to cancel a running job.
4. **Subscribe**: Each podcast has its own feed at `{BASE_URL}/podcast/{guid}/feed.xml` (copy button). Add to any podcast app. Audio supports HTTP range requests for seeking.

**Notes:** Upgrading from single-podcast version wipes episodes/audio but keeps settings and creates a new podcast. No auth on UI/feed (LAN use only). Per-podcast covers auto-generated from `static/cover.png`.

## Development
```bash
mise run test  # or: uv run pytest -q
```

See `app/` for code layout (FastAPI, SQLite, Wallabag/Kokoro clients, pipeline, RSS).
