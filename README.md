# wallabag-podcast

A single-user, self-hosted web app that turns Wallabag saved articles into a
podcast RSS feed, narrated by Kokoro-FastAPI TTS.

## What it does

A queue-driven pipeline: fetch N random unread Wallabag articles into an
editable queue, remove the ones you don't want, then generate one MP3 "episode"
per article (spoken title intro + body) via Kokoro-FastAPI. Episodes are served
through a podcast RSS feed you can subscribe to in any podcast app. No
scheduler in v1 — everything is manual.

## Setup

TODO: fill in once the scaffold is complete.

```bash
uv sync
cp .env.example .env   # then fill in your Wallabag credentials
uv run uvicorn app.main:app --reload
```

## Services

Kokoro-FastAPI must run separately (start it before generating audio):

```bash
docker run -p 8880:8880 ghcr.io/remsky/kokoro-fastapi-cpu:latest
```

## Status

MVP under construction — scaffolding only. Task 8 will expand this README.
