"""FastAPI application entry point.

Lifespan initializes the SQLite database, the audio directory, and shared
Wallabag/Kokoro clients (stored on ``app.state`` so tests can swap in mocks).
Serves the server-rendered web UI (queue + settings), the queue action routes,
a JSON polling endpoint used during generation, and the podcast RSS feed at
``/feed.xml``.

Long-running generation runs as a FastAPI BackgroundTask; progress is written
to SQLite and the UI polls ``/queue/status``.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlencode

from fastapi import BackgroundTasks, FastAPI, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .config import get_settings
from .db import (
    connect,
    get_db_path,
    get_queue_episodes,
    get_setting,
    has_staged_episodes,
    init_db,
    set_setting,
)
from .kokoro import KokoroClient
from .pipeline import (
    add_random,
    archive_completed,
    clear_queue,
    generate_all,
    remove_item,
    stats,
)
from .rss import build_feed
from .wallabag import WallabagClient, WallabagError

logger = logging.getLogger(__name__)

_BASE_DIR = Path(__file__).resolve().parent.parent

templates = Jinja2Templates(directory=_BASE_DIR / "templates")

# Allowed range for the articles_per_drive UI tunable.
_ARTICLES_PER_DRIVE_MIN = 1
_ARTICLES_PER_DRIVE_MAX = 50


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    # Ensure DATA_DIR and DATA_DIR/audio exist before the DB is created.
    settings.DATA_DIR.mkdir(parents=True, exist_ok=True)
    (settings.DATA_DIR / "audio").mkdir(parents=True, exist_ok=True)

    init_db(get_db_path())

    # Shared clients, stored on app.state so tests can replace them with mocks.
    app.state.wallabag_client = WallabagClient(settings)
    app.state.kokoro_client = KokoroClient(settings)
    app.state.generating = False
    try:
        yield
    finally:
        await app.state.wallabag_client.aclose()
        await app.state.kokoro_client.aclose()


app = FastAPI(title="wallabag-podcast", lifespan=lifespan)

app.mount("/static", StaticFiles(directory=_BASE_DIR / "static"), name="static")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _redirect(path: str, *, message: str | None = None, error: str | None = None) -> RedirectResponse:
    """303-redirect back to a page with a flash message/error query param."""
    query = {}
    if message is not None:
        query["message"] = message
    if error is not None:
        query["error"] = error
    separator = "&" if "?" in path else "?"
    location = f"{path}{separator}{urlencode(query)}" if query else path
    return RedirectResponse(location, status_code=303)


async def _run_generation(app: FastAPI) -> None:
    """Background task: generate audio for all staged episodes.

    Flips ``app.state.generating`` so the UI can poll /queue/status for live
    progress. Uses the shared clients (tests patch ``generate_all`` itself).
    """
    app.state.generating = True
    try:
        summary = await generate_all(
            app.state.wallabag_client,
            app.state.kokoro_client,
            get_settings(),
        )
        logger.info("Generation finished: %s", summary)
    except Exception:
        logger.exception("Generation run crashed")
    finally:
        app.state.generating = False


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------


@app.get("/")
async def home(request: Request, message: str | None = None, error: str | None = None):
    conn = connect()
    try:
        episodes = get_queue_episodes(conn)
    finally:
        conn.close()
    settings = get_settings()
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "stats": stats(),
            "episodes": episodes,
            "generating": bool(getattr(app.state, "generating", False)),
            "message": message,
            "error": error,
            "feed_title": settings.FEED_TITLE,
            "feed_author": settings.FEED_AUTHOR,
            "base_url": settings.BASE_URL,
        },
    )


@app.get("/settings")
async def settings_page(
    request: Request, message: str | None = None, error: str | None = None
):
    # Wallabag connection status (never the credentials themselves).
    try:
        wallabag_ok = bool(await app.state.wallabag_client.test_connection())
    except Exception:
        logger.exception("Wallabag connection test failed")
        wallabag_ok = False

    # Voice choices come live from Kokoro; fall back to the configured default.
    kokoro_error: str | None = None
    try:
        voices = await app.state.kokoro_client.voices()
    except Exception as exc:
        logger.warning("Could not fetch Kokoro voices: %s", exc)
        voices = []
        kokoro_error = str(exc)
    if not voices:
        voices = [get_settings().KOKORO_DEFAULT_VOICE]

    conn = connect()
    try:
        articles_per_drive = get_setting(conn, "articles_per_drive") or "10"
        voice = get_setting(conn, "voice") or voices[0]
        automation_time = get_setting(conn, "automation_time") or "07:00"
    finally:
        conn.close()

    settings = get_settings()
    return templates.TemplateResponse(
        request,
        "settings.html",
        {
            "message": message,
            "error": error,
            "wallabag_ok": wallabag_ok,
            "wallabag_url": settings.WALLABAG_URL,
            "kokoro_error": kokoro_error,
            "voices": voices,
            "articles_per_drive": articles_per_drive,
            "voice": voice,
            "automation_time": automation_time,
            "articles_min": _ARTICLES_PER_DRIVE_MIN,
            "articles_max": _ARTICLES_PER_DRIVE_MAX,
            "feed_title": settings.FEED_TITLE,
            "feed_author": settings.FEED_AUTHOR,
            "base_url": settings.BASE_URL,
        },
    )


# ---------------------------------------------------------------------------
# Queue actions
# ---------------------------------------------------------------------------


@app.post("/queue/add-random")
async def queue_add_random():
    conn = connect()
    try:
        n = int(get_setting(conn, "articles_per_drive") or "10")
    finally:
        conn.close()
    try:
        count = await add_random(n, app.state.wallabag_client, get_settings())
    except WallabagError as exc:
        return _redirect("/", error=str(exc))
    if count == 0:
        return _redirect("/", message="No new articles to add")
    return _redirect("/", message=f"Added {count} random articles")


@app.post("/queue/{episode_id}/remove")
async def queue_remove(episode_id: int):
    try:
        remove_item(episode_id)
    except ValueError as exc:
        return _redirect("/", error=str(exc))
    return _redirect("/", message="Removed from queue")


@app.post("/queue/generate")
async def queue_generate(background_tasks: BackgroundTasks):
    conn = connect()
    try:
        staged = has_staged_episodes(conn)
    finally:
        conn.close()
    if not staged:
        return _redirect("/", error="No staged articles to generate")
    if getattr(app.state, "generating", False):
        return _redirect("/", error="A generation run is already in progress")
    background_tasks.add_task(_run_generation, app)
    return _redirect("/", message="Now generating your drive")


@app.post("/queue/archive-completed")
async def queue_archive_completed():
    count = archive_completed()
    return _redirect("/", message=f"Archived {count} completed episodes")


@app.post("/queue/clear")
async def queue_clear():
    count = clear_queue()
    return _redirect("/", message=f"Cleared {count} episodes from the queue")


@app.get("/queue/status")
async def queue_status() -> JSONResponse:
    """JSON snapshot polled by the UI while generation is running."""
    conn = connect()
    try:
        episodes = get_queue_episodes(conn)
    finally:
        conn.close()
    return JSONResponse(
        {
            "generating": bool(getattr(app.state, "generating", False)),
            "stats": stats(),
            "episodes": episodes,
        }
    )


# ---------------------------------------------------------------------------
# Settings actions
# ---------------------------------------------------------------------------


@app.post("/settings")
async def settings_save(request: Request):
    form = await request.form()
    raw_articles = str(form.get("articles_per_drive", "")).strip()
    voice = str(form.get("voice", "")).strip()

    try:
        articles_per_drive = int(raw_articles)
    except ValueError:
        return _redirect(
            "/settings", error="Articles per drive must be a number"
        )
    if not _ARTICLES_PER_DRIVE_MIN <= articles_per_drive <= _ARTICLES_PER_DRIVE_MAX:
        return _redirect(
            "/settings",
            error=(
                f"Articles per drive must be between {_ARTICLES_PER_DRIVE_MIN} "
                f"and {_ARTICLES_PER_DRIVE_MAX}"
            ),
        )
    if not voice:
        return _redirect("/settings", error="Voice must not be empty")

    conn = connect()
    try:
        set_setting(conn, "articles_per_drive", str(articles_per_drive))
        set_setting(conn, "voice", voice)
    finally:
        conn.close()
    return _redirect("/settings", message="Settings saved")


@app.post("/wallabag/test")
async def wallabag_test():
    try:
        ok = bool(await app.state.wallabag_client.test_connection())
    except Exception:
        logger.exception("Wallabag connection test failed")
        ok = False
    if ok:
        return _redirect("/settings", message="Wallabag connection OK")
    return _redirect("/settings", error="Wallabag connection failed")


# ---------------------------------------------------------------------------
# Feed + health
# ---------------------------------------------------------------------------


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/feed.xml")
async def feed() -> Response:
    return Response(content=build_feed(), media_type="application/rss+xml; charset=utf-8")
