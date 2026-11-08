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

### Pocket TTS (serve)
```
TTS_BASE_URL=http://localhost:8880
TTS_DEFAULT_VOICE=charles
TTS_MAX_CHUNK_CHARS=2000         # max chars per TTS request; longer text is split into streamed chunks (sentence + pause-token boundaries; bounded RAM + per-chunk progress)
```

There are no speech-rate or audio-format variables: Pocket TTS has no such
parameters (it streams WAV at 24 kHz mono; the app re-encodes to CBR 64 kbps
MP3 via lameenc).

### App
```
BASE_URL=http://localhost:8000     # reachable address for enclosure URLs; set to LAN IP for phone
HOST=127.0.0.1
PORT=8000
DATA_DIR=./data
EXCLUDE_TAGS=computer,interactive  # comma-separated; client-side filtered (Wallabag API only AND-includes)
MIN_TEXT_CHARS=200                 # skip articles whose cleaned text is shorter
MAX_FETCH_PAGES=50                # safety cap when enumerating unread candidates
EPISODE_GAP_SECONDS=3             # seconds of trailing silence appended to each episode's mp3 (floored to whole 1-second units, generated in-process by the same MP3 encoder as speech; 0 disables)
FEED_TITLE=My Wallabag Podcast   # app BRAND title — shown in the header, footer, and settings page; NOT an RSS feed title. Each podcast feed is titled by its podcast name and served at {BASE_URL}/podcast/{guid}/feed.xml
LOG_LEVEL=INFO                     # DEBUG/INFO/WARNING/ERROR; controls app + uvicorn loggers; a rotating log file is written to DATA_DIR/logs/wallabag-podcast.log (5MB x3 backups)
PRONUNCIATIONS=JSON=Jason,SQL=sequel  # comma-separated KEY=SPOKEN pairs; whole-word, case-insensitive rewrites before TTS (empty by default)
```

## UI-tunable settings (persisted in SQLite `settings` table; override .env defaults at runtime)
- `articles_per_drive` (int, default 10) — the "Add Random N" count
- `voice` (str, default = `TTS_DEFAULT_VOICE`) — choices are the static Pocket TTS `BUILTIN_VOICES` list (21 English built-ins); the settings save validates against it, and `init_db` resets non-empty stored values outside the list back to the default (idempotent, runs on every boot); blank/NULL rows are left as-is and fall back to `TTS_DEFAULT_VOICE` at read time (settings page and generation).
- `automation_enabled` (bool, false in v1 — UI shows it as "coming soon"/disabled)
- `automation_time` (str, e.g. "07:00")

## Rule
Secrets never appear in the UI or DB. The UI shows Wallabag **connection status** (ok/fail) and a "Test Connection" button, not the password or token.