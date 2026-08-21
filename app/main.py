"""FastAPI application entry point.

Only scaffolding for now: lifespan initializes the SQLite database and audio
directory on startup, plus a health route and a placeholder root route. Routes
for the queue, RSS feed, and UI arrive in later tasks.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from .config import get_settings
from .db import get_db_path, init_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    # Ensure DATA_DIR and DATA_DIR/audio exist before the DB is created.
    settings.DATA_DIR.mkdir(parents=True, exist_ok=True)
    (settings.DATA_DIR / "audio").mkdir(parents=True, exist_ok=True)

    init_db(get_db_path())
    yield


app = FastAPI(title="wallabag-podcast", lifespan=lifespan)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/")
async def root() -> str:
    return "wallabag-podcast — UI coming in a later task."
