"""FastAPI application entry point.

Lifespan initializes the SQLite database, the audio directory, and shared
Wallabag/Kokoro clients (stored on ``app.state`` so tests can swap in mocks).
Serves the server-rendered web UI (queue + settings), the queue action routes,
a JSON polling endpoint used during generation, and the podcast RSS feed at
``/feed.xml``.

Long-running generation runs as an asyncio task (handle on
``app.state.generation_task``) so it can be cancelled via POST /queue/stop;
progress is written to SQLite and the UI polls ``/queue/status``.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlencode

from fastapi import FastAPI, Request, Response
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .config import get_settings
from .db import (
    connect,
    get_db_path,
    get_episode_status,
    get_queue_episodes,
    get_setting,
    has_staged_episodes,
    init_db,
    reset_failed_to_staged,
    set_setting,
)
from .kokoro import KokoroClient
from .logging_setup import configure_logging
from .pipeline import (
    add_random,
    archive_item,
    clear_queue,
    delete_item,
    generate_all,
    stats,
)
from .rss import build_feed
from .wallabag import WallabagClient, WallabagError

logger = logging.getLogger(__name__)

_BASE_DIR = Path(__file__).resolve().parent.parent

templates = Jinja2Templates(directory=_BASE_DIR / "templates")


def _human_duration(minutes: int | None) -> str:
    """Render minutes as full words, e.g. '1 day, 3 hours, 15 minutes'.

    Zero-value units are skipped and units are pluralized correctly;
    an empty duration renders as '0 minutes'.
    """
    minutes = int(minutes or 0)
    days, rest = divmod(minutes, 1440)
    hours, mins = divmod(rest, 60)
    parts = [
        f"{value} {unit}{'s' if value != 1 else ''}"
        for value, unit in ((days, "day"), (hours, "hour"), (mins, "minute"))
        if value
    ]
    return ", ".join(parts) if parts else "0 minutes"


templates.env.filters["human_duration"] = _human_duration


def _static_url(path: str) -> str:
    """Versioned /static URL (mtime query) so browsers re-fetch changed assets
    instead of serving a heuristically-cached stale copy."""
    try:
        version = int((_BASE_DIR / "static" / path).stat().st_mtime)
    except OSError:
        version = 0
    return f"/static/{path}?v={version}"


templates.env.globals["static_url"] = _static_url

# Allowed range for the articles_per_drive UI tunable.
_ARTICLES_PER_DRIVE_MIN = 1
_ARTICLES_PER_DRIVE_MAX = 50

# Chunk size when streaming a byte range of an audio file (206 responses).
_AUDIO_CHUNK_SIZE = 64 * 1024


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    # Ensure DATA_DIR and DATA_DIR/audio exist before the DB is created.
    settings.DATA_DIR.mkdir(parents=True, exist_ok=True)
    (settings.DATA_DIR / "audio").mkdir(parents=True, exist_ok=True)

    init_db(get_db_path())

    # Configure logging AFTER DATA_DIR exists and BEFORE yielding so every
    # request and the generation pipeline write to the rotating log file
    # under DATA_DIR/logs/. (uvicorn's own loggers were already configured
    # during server startup; configure_logging preserves them.)
    configure_logging(settings)

    # Shared clients, stored on app.state so tests can replace them with mocks.
    app.state.wallabag_client = WallabagClient(settings)
    app.state.kokoro_client = KokoroClient(settings)
    app.state.generating = False
    app.state.generation_task = None
    try:
        yield
    finally:
        # Cancel and await any lingering generation task so the app exits
        # cleanly before the shared clients are closed.
        task = getattr(app.state, "generation_task", None)
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
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


def _json_or_redirect(request: Request, path: str, *, message: str | None = None, error: str | None = None):
    """Return JSON for AJAX requests, otherwise a 303 redirect."""
    if "application/json" in request.headers.get("accept", ""):
        payload: dict[str, object] = {"ok": error is None}
        if message is not None:
            payload["message"] = message
        if error is not None:
            payload["error"] = error
        return JSONResponse(payload)
    return _redirect(path, message=message, error=error)


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
        app.state.generation_task = None


def _parse_range(range_header: str, file_size: int) -> tuple[int, int] | None:
    """Parse a single byte range from a Range header.

    Returns the inclusive (start, end) pair, or None when the header is not
    a usable single-range request. Supports open-ended ranges (``bytes=500-``
    = from byte 500 to EOF) and suffix ranges (``bytes=-500`` = the last 500
    bytes). The end is clamped to ``file_size - 1`` and a suffix longer than
    the file returns the whole file. Returns None when the range cannot be
    satisfied (start >= file_size, or start > end). Multi-range requests are
    collapsed to their first range.
    """
    if not range_header or file_size <= 0:
        return None
    # Only the "bytes" unit is supported; ignore everything else.
    if not range_header.startswith("bytes="):
        return None
    # RFC 7233 allows ignoring a multi-range request, so only the first
    # range is parsed and served.
    spec = range_header.removeprefix("bytes=").split(",", 1)[0].strip()
    start_s, _, end_s = spec.partition("-")
    if start_s == "" and end_s == "":
        return None

    if start_s == "":
        # Suffix range: the last N bytes of the file.
        try:
            suffix = int(end_s)
        except ValueError:
            return None
        if suffix <= 0:
            return None
        start = max(0, file_size - suffix)
        end = file_size - 1
    else:
        try:
            start = int(start_s)
        except ValueError:
            return None
        if end_s == "":
            end = file_size - 1
        else:
            try:
                end = int(end_s)
            except ValueError:
                return None
            end = min(end, file_size - 1)

    if start < 0 or start >= file_size or start > end:
        return None
    return start, end


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------


@app.get("/")
async def home(request: Request, message: str | None = None, error: str | None = None):
    conn = connect()
    try:
        episodes = get_queue_episodes(conn)
        articles_per_drive = int(get_setting(conn, "articles_per_drive") or "10")
    finally:
        conn.close()
    settings = get_settings()
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "stats": stats(),
            "articles_per_drive": articles_per_drive,
            "episodes": episodes,
            "generating": bool(getattr(app.state, "generating", False)),
            "message": message,
            "error": error,
            "feed_title": settings.FEED_TITLE,
            "base_url": settings.BASE_URL,
            "wallabag_url": settings.WALLABAG_URL,
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
        default_id = get_settings().KOKORO_DEFAULT_VOICE
        voices = [{"id": default_id, "label": default_id}]

    conn = connect()
    try:
        articles_per_drive = get_setting(conn, "articles_per_drive") or "10"
        voice = get_setting(conn, "voice") or voices[0]["id"]
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


@app.get("/episode/{episode_id}/delete")
async def confirm_delete(request: Request, episode_id: int):
    conn = connect()
    try:
        row = conn.execute(
            "SELECT title, status FROM episodes WHERE id=?", (episode_id,)
        ).fetchone()
    finally:
        conn.close()
    if row is None or row[1] == "archived":
        return _redirect("/", error="Episode not found")
    settings = get_settings()
    return templates.TemplateResponse(
        request,
        "confirm-delete.html",
        {
            "episode_id": episode_id,
            "title": row[0],
            "feed_title": settings.FEED_TITLE,
        },
    )


@app.post("/queue/{episode_id}/delete")
async def queue_delete(request: Request, episode_id: int):
    conn = connect()
    try:
        status = get_episode_status(conn, episode_id)
    finally:
        conn.close()
    # During an active run the loop owns the generating row: trigger stop
    # instead of deleting; the loop marks it failed and the page reload
    # (via /queue/status polling) shows the now-failed row with its delete btn.
    if status == "generating" and getattr(app.state, "generating", False):
        task = getattr(app.state, "generation_task", None)
        if task is not None and not task.done():
            task.cancel()
        return _json_or_redirect(request, "/", message="Stopping generation...")
    try:
        delete_item(episode_id)
    except ValueError as exc:
        return _json_or_redirect(request, "/", error=str(exc))
    return _json_or_redirect(request, "/", message="Removed from podcast (article stays unread in Wallabag)")


@app.post("/queue/{episode_id}/archive")
async def queue_archive(request: Request, episode_id: int):
    try:
        await archive_item(episode_id, app.state.wallabag_client)
    except ValueError as exc:
        return _json_or_redirect(request, "/", error=str(exc))
    except WallabagError as exc:
        return _json_or_redirect(
            request,
            "/",
            error=f"Could not mark article as read in Wallabag: {exc}",
        )
    return _json_or_redirect(request, "/", message="Article marked read in Wallabag (episode kept in podcast)")


@app.post("/queue/generate")
async def queue_generate():
    conn = connect()
    try:
        # Failed episodes are retryable: sweep them back into the queue so
        # Generate Audio picks them up alongside newly staged episodes.
        reset_failed_to_staged(conn)
        staged = has_staged_episodes(conn)
    finally:
        conn.close()
    if not staged:
        return _redirect("/", error="No staged articles to generate")
    if getattr(app.state, "generating", False):
        return _redirect("/", error="A generation run is already in progress")
    app.state.generation_task = asyncio.create_task(_run_generation(app))
    return _redirect("/", message="Now generating audio")


@app.post("/queue/stop")
async def queue_stop():
    task = getattr(app.state, "generation_task", None)
    if task is None or task.done():
        return _redirect("/", error="No generation run to stop")
    task.cancel()
    return _redirect("/", message="Stopping generation...")


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

    error: str | None = None
    try:
        articles_per_drive = int(raw_articles)
    except ValueError:
        error = "Articles per drive must be a number"
    if error is None and not (
        _ARTICLES_PER_DRIVE_MIN <= articles_per_drive <= _ARTICLES_PER_DRIVE_MAX
    ):
        error = (
            f"Articles per drive must be between {_ARTICLES_PER_DRIVE_MIN} "
            f"and {_ARTICLES_PER_DRIVE_MAX}"
        )
    if error is None and not voice:
        error = "Voice must not be empty"

    # The autosave UI posts via fetch and consumes JSON; plain form posts
    # (no-JS fallback) still get the classic redirect + flash behavior.
    wants_json = "application/json" in request.headers.get("accept", "")

    if error is not None:
        if wants_json:
            return JSONResponse({"ok": False, "error": error}, status_code=400)
        return _redirect("/settings", error=error)

    conn = connect()
    try:
        set_setting(conn, "articles_per_drive", str(articles_per_drive))
        set_setting(conn, "voice", voice)
    finally:
        conn.close()
    if wants_json:
        return JSONResponse({"ok": True})
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


@app.get("/audio/{episode_id}.mp3")
async def audio(episode_id: int, request: Request) -> Response:
    """Serve a generated episode MP3, honoring HTTP Range requests.

    Podcast apps issue byte-range requests to seek/scrub; a 206 Partial
    Content response with the requested slice lets them do so. Without a
    Range header the whole file is returned as 200. The episode's
    ``audio_path`` is looked up directly because db.py has no per-episode
    audio_path getter (kept inline to avoid expanding the repository).
    """
    conn = connect()
    try:
        row = conn.execute(
            "SELECT audio_path FROM episodes WHERE id=?", (episode_id,)
        ).fetchone()
    finally:
        conn.close()
    if row is None or not row[0]:
        return Response(status_code=404)

    audio_path = Path(row[0])
    if not audio_path.is_file():
        return Response(status_code=404)
    file_size = audio_path.stat().st_size

    headers = {"Accept-Ranges": "bytes"}
    range_header = request.headers.get("range")
    if not range_header:
        return FileResponse(audio_path, media_type="audio/mpeg", headers=headers)

    parsed = _parse_range(range_header, file_size)
    if parsed is None:
        return Response(
            status_code=416,
            headers={"Content-Range": f"bytes */{file_size}"},
        )

    start, end = parsed
    length = end - start + 1
    headers["Content-Range"] = f"bytes {start}-{end}/{file_size}"
    headers["Content-Length"] = str(length)

    async def _iter_chunks():
        with audio_path.open("rb") as audio_file:
            audio_file.seek(start)
            remaining = length
            while remaining > 0:
                chunk = audio_file.read(min(_AUDIO_CHUNK_SIZE, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                yield chunk

    return StreamingResponse(
        _iter_chunks(),
        status_code=206,
        media_type="audio/mpeg",
        headers=headers,
    )
