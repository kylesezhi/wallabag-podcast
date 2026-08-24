# wallabag-podcast

A single-user, self-hosted web app that turns your Wallabag saved articles
into a podcast: it pulls random unread articles into an editable queue,
synthesizes each one into an MP3 "episode" with Kokoro-FastAPI text-to-speech,
and serves them through a podcast RSS feed you can subscribe to in any podcast
app.

## What it does

A queue-driven workflow: fetch N random unread Wallabag articles → review and
edit the queue (drop the ones you don't want) → generate one MP3 per article
via Kokoro TTS → subscribe to the resulting podcast feed. Each episode is a
spoken title intro followed by the article body. There is no scheduler in v1 —
everything is manual, and the app never mutates your Wallabag state.

## Prerequisites

- **Python 3.11+** and **[uv](https://docs.astral.sh/uv/)** (or **Docker** for
  the containerized setup)
- A **Wallabag** instance (self-hosted or https://app.wallabag.it) with API
  credentials enabled — get `CLIENT_ID` / `CLIENT_SECRET` from your
  instance's *Developer* / *API clients* settings page
- **Kokoro-FastAPI** running locally (see below) so MP3s can be generated

## Quick start (local dev)

```bash
uv sync
cp .env.example .env        # then fill in your Wallabag credentials
```

Start Kokoro-FastAPI (the TTS engine) in a separate terminal:

```bash
docker run -p 8880:8880 ghcr.io/remsky/kokoro-fastapi-cpu:latest
```

Then run the app:

```bash
just run                    # or: uv run uvicorn app.main:app --reload
```

Open <http://127.0.0.1:8000> — you should see the empty queue page. Click
**Settings** → **Test Connection** to verify Wallabag is reachable.

## Docker setup

```bash
cp .env.example .env        # then fill in your Wallabag credentials
docker compose up -d
```

Open <http://localhost:8000>. Compose starts **both** the app and a
Kokoro-FastAPI container, so no separate TTS step is needed. Inside the
compose network the app reaches Kokoro via the service name `kokoro`, so
`KOKORO_BASE_URL` is overridden to `http://kokoro:8880` (the `.env` default of
`http://localhost:8880` only works for local dev). `HOST=0.0.0.0` is also
overridden so the app is reachable outside its container. Generated audio and
the SQLite database live in `./data` on the host (a bind mount), so they
survive container rebuilds.

## Configuration (.env)

Copy `.env.example` to `.env` and fill it in. All variables:

| Variable | Description |
| --- | --- |
| `WALLABAG_URL` | Base URL of your Wallabag instance (default `https://app.wallabag.it`). |
| `WALLABAG_CLIENT_ID` | OAuth client ID — create one in your Wallabag instance's *Developer* / *API clients* settings. |
| `WALLABAG_CLIENT_SECRET` | OAuth client secret for the client above. |
| `WALLABAG_USERNAME` | Your Wallabag username (OAuth password grant). |
| `WALLABAG_PASSWORD` | Your Wallabag password (OAuth password grant). |
| `KOKORO_BASE_URL` | Where Kokoro-FastAPI listens (default `http://localhost:8880`). |
| `KOKORO_DEFAULT_VOICE` | Default TTS voice, e.g. `af_heart` (overridable in the Settings UI). |
| `KOKORO_SPEED` | Speech rate multiplier passed to Kokoro (default `1.0`). |
| `KOKORO_RESPONSE_FORMAT` | Audio format requested from Kokoro (default `mp3`). |
| `BASE_URL` | **The most important setting for phone listening.** The address podcast apps use for audio/enclosure URLs. Use `http://localhost:8000` when listening on the same machine, or your computer's LAN IP (e.g. `http://192.168.1.50:8000`) to listen from your phone on the same network. |
| `HOST` | Bind address for uvicorn (default `127.0.0.1`; use `0.0.0.0` to expose on the LAN). |
| `PORT` | Port uvicorn listens on (default `8000`). |
| `DATA_DIR` | Where the SQLite DB and generated `audio/*.mp3` live (default `./data`). |
| `EXCLUDE_TAGS` | Comma-separated Wallabag tags to skip when picking random articles (e.g. `computer,interactive`). |
| `MIN_TEXT_CHARS` | Minimum cleaned-text length for an article to be worth narrating; shorter articles are skipped (default `200`). |
| `MAX_FETCH_PAGES` | Safety cap on pages fetched while enumerating unread candidates (default `50`). |
| `FEED_TITLE` | Podcast feed title shown in podcast apps. |

Wallabag credentials are OAuth password-grant values — they never appear in
the UI or the database, only in `.env`.

## Using the app

1. Open the home page. Click **Add Random** to stage N random unread articles
   (N is set under **Settings** → *Articles per drive*, default 10).
2. Review the queue and click **Delete** on any article you don't want.
   Deleting also marks the article as read (archived) in Wallabag — if that
   fails, nothing is deleted and the episode stays queued so you can retry.
3. Click **Generate Audio** — each article is fetched, cleaned, and
   synthesized into an MP3. Progress updates live on the page as episodes flip
   from `generating` to `done` (or `failed` with a reason).
4. To stop a run in progress — e.g. if synthesis is hung — click **Stop
   Generating** (shown in the progress card). The in-flight episode is marked
   `failed` ("Cancelled by user") and the remaining queued episodes stay
   `staged` so you can generate them later.
5. When you're happy with the drive, click **Delete** on any finished episode
   to remove it from the feed and delete its MP3 file — you'll get a quick
   confirm prompt first since the audio is gone for good. Deleting an episode
   also marks its Wallabag article as read, so **Add Random** won't offer it
   again (archived articles are never re-enumerated). **Clear Staged** drops
   the staged/failed items without touching completed ones.
6. An episode can get stuck in `generating` if the process is restarted
   mid-run. Its **Delete** button always appears, so you can remove it like
   any other item (marking the article read in Wallabag). Clicking **Delete**
   on the in-flight episode during an active run stops the run first — the
   episode flips to `failed` and you can delete it again — or use **Stop
   Generating** directly.

## Subscribing in a podcast app

The feed URL is `{BASE_URL}/feed.xml` (shown on the home page). Add it to any
podcast app — Apple Podcasts, Podcast Addict, AntennaPod, etc. — as an RSS
feed.

For phone listening on the same LAN, set `BASE_URL` to your computer's LAN IP
(e.g. `http://192.168.1.50:8000`) and bind `HOST=0.0.0.0` (or use the Docker
setup). There is no authentication on the feed or the UI — it's intended for
local-network use only.

## Cover art

The podcast uses a single cover image for the show and for every episode. The
asset lives at `static/cover.png` (copied from `reference/cover.png`, the
design source) and is served at `{BASE_URL}/static/cover.png`; the feed
references it via the iTunes image element at both the channel and episode
level, plus the legacy RSS `<image>` element. The image is 1254×1254, which is
below Apple Podcasts' 1400px directory minimum, but it's valid RSS and fine
for self-hosted LAN use.

## Seeking / range support

Audio is served with **HTTP 206 Partial Content** range support, so podcast
apps can seek and scrub within episodes instead of re-downloading the whole
file. Every audio response advertises `Accept-Ranges: bytes`; invalid or
unsatisfiable ranges get a `416 Range Not Satisfiable`.

## Development

```bash
just test                  # or: uv run pytest -q
```

Project layout:

```
app/
  main.py        # FastAPI app, routes, lifespan (UI + queue + feed + audio)
  config.py      # pydantic-settings Settings — secrets + defaults from .env
  db.py          # SQLite connection, schema init, repository functions
  wallabag.py    # WallabagClient: oauth, list metadata, get entry
  kokoro.py      # KokoroClient: voices, synthesize -> mp3 bytes
  textclean.py   # HTML -> clean spoken text + intro assembly
  pipeline.py    # orchestration: queue ops, generate flow
  rss.py         # build podcast feed from episodes
templates/       # Jinja2: base, index (drive+queue), settings
static/          # css, js, images
tests/           # pytest suite
```

## How it works

FastAPI serves a server-rendered UI backed by a thin SQLite repository. The
pipeline fetches unread article metadata from the **Wallabag API**, filters
exclusions (tags, already-processed articles), and stages random picks. During
generation each staged episode's full entry is fetched, cleaned from HTML into
spoken text, and sent to **Kokoro-FastAPI**, which returns an MP3 written to
`DATA_DIR/audio/{id}.mp3`; duration is measured and the episode is marked
done. The **RSS feed** (built with feedgen) references those files at
`{BASE_URL}/audio/{id}.mp3`, which the range-aware audio route serves.
All queue/processed tracking is local to SQLite — Wallabag state is never
mutated.
