# Spec: config-and-env

Scope: repo

# Config &amp; Environment

## .env (secrets + defaults) — never committed; `.env.example` is the template.

### Wallabag (OAuth password grant)
```
WALLABAG_URL=https://app.wallabag.it
WALLABAG_CLIENT_ID=...
WALLABAG_CLIENT_SECRET=...
WALLABAG_USERNAME=...
WALLABAG_PASSWORD=...
```

### Kokoro-FastAPI
```
KOKORO_BASE_URL=http://localhost:8880
KOKORO_DEFAULT_VOICE=af_heart
KOKORO_SPEED=1.0
KOKORO_RESPONSE_FORMAT=mp3
KOKORO_MAX_CHUNK_CHARS=2000      # max chars per TTS request; longer text is split into sentence-boundary chunks synthesized one at a time (bounded RAM + per-chunk progress)
```

### App
```
BASE_URL=http://localhost:8000     # reachable address for enclosure URLs; set to LAN IP for phone
HOST=127.0.0.1
PORT=8000
DATA_DIR=./data
EXCLUDE_TAGS=computer,interactive  # comma-separated; client-side filtered (Wallabag API only AND-includes)
MIN_TEXT_CHARS=200                 # skip articles whose cleaned text is shorter
MAX_FETCH_PAGES=50                # safety cap when enumerating unread candidates
EPISODE_GAP_SECONDS=3             # seconds of trailing silence appended to each episode's mp3 (floored to whole 1-second units of the packaged silent asset; 0 disables)
FEED_TITLE=My Wallabag Podcast
PRONUNCIATIONS=JSON=Jason,SQL=sequel  # comma-separated KEY=SPOKEN pairs; whole-word, case-insensitive rewrites before TTS (empty by default)
```

## UI-tunable settings (persisted in SQLite `settings` table; override .env defaults at runtime)
- `articles_per_drive` (int, default 10) — the "Add Random N" count
- `voice` (str, default = `KOKORO_DEFAULT_VOICE`) — choices fetched live from Kokoro `GET /v1/audio/voices`
- `automation_enabled` (bool, false in v1 — UI shows it as "coming soon"/disabled)
- `automation_time` (str, e.g. "07:00")

## Rule
Secrets never appear in the UI or DB. The UI shows Wallabag **connection status** (ok/fail) and a "Test Connection" button, not the password or token.