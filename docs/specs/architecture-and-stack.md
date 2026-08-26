# Spec: architecture-and-stack

Scope: repo

# Architecture &amp; Stack

## Stack
- **Language:** Python 3.11+
- **Web framework:** FastAPI + Uvicorn (ASGI)
- **Templating:** Jinja2 (server-rendered) + HTMX for interactivity + a small hand-written CSS
- **HTTP client:** httpx (async) for Wallabag and Kokoro
- **HTML parsing:** BeautifulSoup4 + lxml
- **Audio metadata/duration:** mutagen
- **RSS:** feedgen (podcast RSS 2.0 + iTunes extensions)
- **Config:** pydantic-settings (reads .env)
- **DB:** SQLite via stdlib sqlite3 (no ORM; a thin repository module in db.py)
- **No scheduler in v1.** Automation is a fast-follow; the ops layer (pipeline.py) is structured so an APScheduler in-process job can be added later without rework.

## Project layout
```
wallabag-podcast/
  app/
    __init__.py
    main.py          # FastAPI app, routes, lifespan
    config.py        # Settings (pydantic-settings) — secrets + defaults from .env
    db.py            # SQLite connection, schema init, repository functions
    wallabag.py      # WallabagClient: oauth, list metadata, get entry, refresh
    kokoro.py        # KokoroClient: voices, synthesize -> mp3 bytes
    textclean.py     # HTML -> clean spoken text + intro assembly
    pipeline.py      # orchestration: queue ops, generate flow
    rss.py           # build podcast feed from episodes
    logging_setup.py # configure_logging(): console + rotating file under DATA_DIR/logs/
  templates/         # Jinja2: base, index (drive+queue), settings, partials
  static/            # css, js, images
  data/              # gitignored: podcast.db, audio/*.mp3
  tests/             # pytest
  .env.example
  pyproject.toml
  docker-compose.yml # app + kokoro-fastapi-cpu
  README.md
```

## Conventions
- **Secrets** (Wallabag creds, Kokoro URL, BASE_URL) live ONLY in .env / env vars — never in the DB or UI.
- **Tunables** (articles_per_drive, voice, schedule placeholder) are editable in the UI, persisted in the SQLite `settings` table, and override .env defaults at runtime.
- **Long-running generation** runs as a FastAPI BackgroundTask; progress + per-item status are written to SQLite and the UI polls.
- **No mutation of Wallabag state.** All "processed" tracking is local (SQLite `processed_articles` index).
- **Bind to 127.0.0.1 by default** (local network only; no auth on feed or UI in v1). BASE_URL is configurable so a phone on the same LAN can reach enclosure URLs.
- **Chunked TTS generation** — each episode's TTS input is split client-side into sentence-boundary chunks (max `KOKORO_MAX_CHUNK_CHARS` chars); chunks are synthesized one at a time and appended straight to `data/audio/{id}.mp3.part`, so only one chunk's audio is ever in RAM and long articles cannot exhaust memory. The finished part file is atomically renamed to `{id}.mp3`; per-chunk progress is persisted for the UI.
- **Logging** — `configure_logging()` runs once in `lifespan()` (after `init_db`): console + a `RotatingFileHandler` at `DATA_DIR/logs/wallabag-podcast.log` (5 MB × 3 backups; `DATA_DIR` is volume-mounted so logs survive container restarts). `LOG_LEVEL` (default INFO) governs root/`app`/`uvicorn`; `uvicorn.access` is quieted to WARNING. Per-episode failures store a short reason in `episodes.error` (queue UI) and write the full traceback to the log file; run start/finish and per-episode start/done are logged at INFO.
- **Tests:** pytest, httpx mock transports for Wallabag/Kokoro, sample Wallabag HTML fixtures for the cleaner.