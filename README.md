# wallabag-podcast

Turn your Wallabag saved articles into podcasts. Create multiple randomly-named podcasts, queue random unread articles, generate MP3s with [Kyutai Pocket TTS](https://github.com/kyutai-labs/pocket-tts), and subscribe via per-podcast RSS feeds. Articles are never repeated across podcasts; Wallabag state is never modified.

Pocket TTS loads a **single language model (English)** per instance — non-English articles will sound wrong.

## Quick Start

### Local dev
```bash
uv sync
cp .env.example .env  # fill in Wallabag credentials
# Start TTS in another terminal (first run downloads the English model from
# Hugging Face — takes a few minutes; the CPU index avoids ~3 GB of CUDA
# torch wheels on Linux; port 8880 matches TTS_BASE_URL's default)
uvx --index https://download.pytorch.org/whl/cpu pocket-tts serve --port 8880
# Run app
mise run start  # or: uv run uvicorn app.main:app --reload
```
Open http://127.0.0.1:8000. First start creates a podcast automatically. Test Wallabag in **Settings** → **Test Connection**; the settings page also shows whether the TTS server is reachable and offers the built-in voice list (audition voices on the [Kyutai demo page](https://kyutai.org/pocket-tts) or Pocket TTS's own web UI at http://localhost:8880).

### Docker
```bash
cp .env.example .env
docker compose up -d --build
```
Opens at http://localhost:8000. Compose runs both the app and a Pocket TTS server — the `tts` service builds from the repo's own `Dockerfile.tts`, which installs the pinned `pocket-tts` package with **CPU-only torch** from the PyTorch CPU index (the Linux approach upstream documents; it avoids ~3 GB of unused CUDA wheels PyPI's default torch bundles), and the model downloads from Hugging Face on first start (cached in named volumes, so restarts are fast). Inside the compose network `TTS_BASE_URL` is overridden to `http://tts:8880`; audio/DB persist in `./data`. Set `BASE_URL` to your LAN IP for phone access. The tts service also publishes port 8880, so Pocket TTS's web UI for auditioning voices is reachable at http://localhost:8880.

## Configuration

Key variables in `.env`:
| Variable | Description |
| --- | --- |
| `WALLABAG_URL`, `WALLABAG_CLIENT_ID`, `WALLABAG_CLIENT_SECRET`, `WALLABAG_USERNAME`, `WALLABAG_PASSWORD` | Wallabag OAuth credentials (never stored in DB/UI). |
| `TTS_BASE_URL` | Where Pocket TTS listens (default `http://localhost:8880`). |
| `TTS_DEFAULT_VOICE` | Default TTS voice, a Pocket TTS built-in name (default `charles`; overridable in Settings). |
| `TTS_MAX_CHUNK_CHARS` | Max characters per TTS request (default 2000); longer text is split into chunks synthesized one at a time. |
| `BASE_URL` | Feed/audio base URL for podcast apps (use LAN IP for phones). |
| `HOST`/`PORT` | Bind address/port (`0.0.0.0` to expose on LAN). |
| `DATA_DIR` | DB and audio location (default `./data`). |
| `EXCLUDE_TAGS`, `MIN_TEXT_CHARS`, `MAX_FETCH_PAGES` | Filtering options. |

There are no speech-rate or audio-format settings — Pocket TTS has no such parameters.

### Upgrading from the previous TTS engine version

- Remove the previous engine's settings from your `.env` — the TTS lines that no longer appear in `.env.example` or the table above (base URL/voice plus the speed and format entries, which no longer exist) — and replace them with the `TTS_*` variables above.
- A stored voice from the old engine (e.g. `bm_daniel`) is automatically reset to the configured `TTS_DEFAULT_VOICE` (default `charles`) on the first boot — the warning appears once in the startup console output (`docker logs` for the compose setup, your terminal for local dev); it never reaches the rotating log file because it fires before file logging is configured, and subsequent boots are silent since the voice is already valid.
- Existing episodes and audio keep playing; only newly generated episodes use Pocket TTS.

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

See `app/` for code layout (FastAPI, SQLite, Wallabag/Pocket TTS clients, pipeline, RSS).

## How it works

During generation, each episode's article is cleaned from HTML into spoken text with **pause directives** (`[pause:Xs]` tokens around the title, section headings, and bold-paragraph headings). The text is split at sentence boundaries and at every pause token — a token always leads its chunk — and the pipeline processes chunks in order: each chunk's leading pause tokens become *generated silence* (encoded in-process by the same lameenc MP3 encoder as the speech, so pause and speech audio are homogeneous), the remaining text is sent to **Pocket TTS** (which returns a WAV that is re-encoded to CBR 64 kbps MP3), and the bytes are appended to the episode file. Section titles become ID3 chapter markers timed from the measured audio; each finished episode ends with a floor(`EPISODE_GAP_SECONDS`) trailing silence as an audible boundary.
