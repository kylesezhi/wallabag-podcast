"""Tests for the generate_all() episode generation pipeline.

Focus is state transitions and per-episode error isolation. Real clients are
built on httpx.MockTransport (Wallabag + Kokoro) and measure_duration is
monkeypatched so no real MP3 files are needed.
"""

import json
import sqlite3

import httpx
import pytest

from app.config import Settings, get_settings
from app.db import get_db_path, init_db
from app.kokoro import KokoroClient
from app.pipeline import generate_all
from app.wallabag import WallabagClient

_REQUIRED_ENV = {
    "WALLABAG_CLIENT_ID": "test_client_id",
    "WALLABAG_CLIENT_SECRET": "test_client_secret",
    "WALLABAG_USERNAME": "test_user",
    "WALLABAG_PASSWORD": "test_pass",
}


def _settings(**overrides) -> Settings:
    defaults = {
        "WALLABAG_URL": "https://wallabag.example.test",
        "WALLABAG_CLIENT_ID": "test_client_id",
        "WALLABAG_CLIENT_SECRET": "test_client_secret",
        "WALLABAG_USERNAME": "test_user",
        "WALLABAG_PASSWORD": "test_pass",
        "KOKORO_BASE_URL": "http://kokoro.example.test",
    }
    defaults.update(overrides)
    return Settings(_env_file=None, **defaults)


def _token_response() -> dict:
    return {
        "access_token": "token-123",
        "expires_in": 3600,
        "refresh_token": "refresh-abc",
        "token_type": "bearer",
    }


def _entry_payload(entry_id: int, content: str, title: str | None = None) -> dict:
    return {
        "id": entry_id,
        "title": title or f"Article {entry_id}",
        "url": f"https://example.com/{entry_id}",
        "domain_name": "example.com",
        "reading_time": 5,
        "language": "en",
        "tags": [],
        "is_archived": 0,
        "is_starred": 0,
        "content": content,
    }


def _long_content() -> str:
    """Enough text to clear MIN_TEXT_CHARS (default 200)."""
    return "<p>" + " ".join(["word"] * 100) + "</p>"


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Point DATA_DIR at tmp_path, init the DB schema, and seed settings."""
    for key, value in _REQUIRED_ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    get_settings.cache_clear()
    init_db(get_db_path())
    return tmp_path


def _insert_staged(conn: sqlite3.Connection, entries: list[tuple[int, str]]) -> None:
    """Insert staged episodes as (wallabag_id, title) pairs."""
    for wallabag_id, title in entries:
        conn.execute(
            "INSERT INTO episodes (wallabag_id, title, source, url, status, "
            "est_minutes, language, created_at) VALUES (?, ?, ?, ?, 'staged', "
            "5, 'en', '2026-01-01T00:00:00+00:00')",
            (
                wallabag_id,
                title,
                f"example.com/{wallabag_id}",
                f"https://example.com/{wallabag_id}",
            ),
        )
    conn.commit()


def _make_wallabag(handler) -> WallabagClient:
    return WallabagClient(
        settings=_settings(),
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )


def _make_kokoro(handler) -> KokoroClient:
    return KokoroClient(
        settings=_settings(),
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )


def _wallabag_ok_handler():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth/v2/token":
            return httpx.Response(200, json=_token_response())
        if request.url.path.startswith("/api/entries/"):
            entry_id = int(request.url.path.split("/")[3].split(".")[0])
            return httpx.Response(
                200,
                json=_entry_payload(entry_id, _long_content()),
            )
        return httpx.Response(404)

    return handler


def _episode_rows(conn: sqlite3.Connection) -> list[dict]:
    conn.row_factory = sqlite3.Row
    return [
        dict(row)
        for row in conn.execute(
            "SELECT id, status, error, audio_path, duration_sec, drive_id "
            "FROM episodes ORDER BY id"
        )
    ]


def _processed_ids(conn: sqlite3.Connection) -> list[int]:
    return [row[0] for row in conn.execute("SELECT wallabag_id FROM processed_articles")]


async def test_happy_path(env, monkeypatch):
    with sqlite3.connect(get_db_path()) as conn:
        _insert_staged(conn, [(1, "Article One"), (2, "Article Two")])

    wallabag = _make_wallabag(_wallabag_ok_handler())
    kokoro = _make_kokoro(lambda request: httpx.Response(200, content=b"FAKE_MP3"))
    monkeypatch.setattr("app.pipeline.measure_duration", lambda audio_path: 60)

    summary = await generate_all(wallabag, kokoro, settings=get_settings())

    assert summary == {"total": 2, "done": 2, "failed": 0, "skipped": 0}

    audio_dir = env / "audio"
    assert (audio_dir / "1.mp3").read_bytes() == b"FAKE_MP3"
    assert (audio_dir / "2.mp3").read_bytes() == b"FAKE_MP3"

    with sqlite3.connect(get_db_path()) as conn:
        episodes = _episode_rows(conn)
        assert [e["status"] for e in episodes] == ["done", "done"]
        assert all(e["error"] is None for e in episodes)
        assert episodes[0]["audio_path"] == str(audio_dir / "1.mp3")
        assert episodes[1]["audio_path"] == str(audio_dir / "2.mp3")
        assert episodes[0]["duration_sec"] == 60
        assert episodes[1]["duration_sec"] == 60
        # All episodes in one run share the same drive_id (first run -> 1).
        assert episodes[0]["drive_id"] == episodes[1]["drive_id"] == 1
        assert _processed_ids(conn) == [1, 2]


async def test_skip_article_is_isolated(env, monkeypatch):
    with sqlite3.connect(get_db_path()) as conn:
        _insert_staged(conn, [(1, "Too Short"), (2, "Article Two")])

    def wallabag_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth/v2/token":
            return httpx.Response(200, json=_token_response())
        if request.url.path == "/api/entries/1.json":
            return httpx.Response(200, json=_entry_payload(1, "<p>hi</p>"))
        if request.url.path == "/api/entries/2.json":
            return httpx.Response(200, json=_entry_payload(2, _long_content()))
        return httpx.Response(404)

    wallabag = _make_wallabag(wallabag_handler)
    kokoro = _make_kokoro(lambda request: httpx.Response(200, content=b"FAKE_MP3"))
    monkeypatch.setattr("app.pipeline.measure_duration", lambda audio_path: 60)

    summary = await generate_all(wallabag, kokoro, settings=get_settings())

    # A skip marks the episode failed (with a descriptive error) and is also
    # tracked separately in "skipped".
    assert summary == {"total": 2, "done": 1, "failed": 1, "skipped": 1}

    with sqlite3.connect(get_db_path()) as conn:
        episodes = _episode_rows(conn)
        assert episodes[0]["status"] == "failed"
        assert "Skipped" in episodes[0]["error"]
        assert episodes[1]["status"] == "done"
        # Skips are NOT recorded in processed_articles.
        assert _processed_ids(conn) == [2]


async def test_kokoro_failure_is_isolated(env, monkeypatch):
    with sqlite3.connect(get_db_path()) as conn:
        _insert_staged(conn, [(1, "Article One"), (2, "Article Two")])

    wallabag = _make_wallabag(_wallabag_ok_handler())
    responses = [
        httpx.Response(500, text="kokoro exploded"),
        httpx.Response(200, content=b"FAKE_MP3"),
    ]
    kokoro = _make_kokoro(lambda request: responses.pop(0))
    monkeypatch.setattr("app.pipeline.measure_duration", lambda audio_path: 60)

    summary = await generate_all(wallabag, kokoro, settings=get_settings())

    assert summary == {"total": 2, "done": 1, "failed": 1, "skipped": 0}

    with sqlite3.connect(get_db_path()) as conn:
        episodes = _episode_rows(conn)
        assert episodes[0]["status"] == "failed"
        assert "500" in episodes[0]["error"]
        assert episodes[1]["status"] == "done"
        assert _processed_ids(conn) == [2]


async def test_wallabag_failure_is_isolated(env, monkeypatch):
    with sqlite3.connect(get_db_path()) as conn:
        _insert_staged(conn, [(1, "Article One"), (2, "Article Two")])

    def wallabag_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth/v2/token":
            return httpx.Response(200, json=_token_response())
        if request.url.path == "/api/entries/1.json":
            raise httpx.ConnectError("wallabag unreachable")
        if request.url.path == "/api/entries/2.json":
            return httpx.Response(200, json=_entry_payload(2, _long_content()))
        return httpx.Response(404)

    wallabag = _make_wallabag(wallabag_handler)
    kokoro = _make_kokoro(lambda request: httpx.Response(200, content=b"FAKE_MP3"))
    monkeypatch.setattr("app.pipeline.measure_duration", lambda audio_path: 60)

    summary = await generate_all(wallabag, kokoro, settings=get_settings())

    assert summary == {"total": 2, "done": 1, "failed": 1, "skipped": 0}

    with sqlite3.connect(get_db_path()) as conn:
        episodes = _episode_rows(conn)
        assert episodes[0]["status"] == "failed"
        assert "Wallabag" in episodes[0]["error"]
        assert episodes[1]["status"] == "done"
        assert _processed_ids(conn) == [2]


async def test_voice_comes_from_db_settings(env, monkeypatch):
    with sqlite3.connect(get_db_path()) as conn:
        _insert_staged(conn, [(1, "Article One")])
        conn.execute(
            "INSERT INTO settings (key, value, updated_at) VALUES "
            "('voice', 'am_michael', 'x') ON CONFLICT(key) DO UPDATE "
            "SET value='am_michael'"
        )
        conn.commit()

    captured = {}

    def kokoro_handler(request: httpx.Request) -> httpx.Response:
        captured["voice"] = json.loads(request.content)["voice"]
        return httpx.Response(200, content=b"FAKE_MP3")

    wallabag = _make_wallabag(_wallabag_ok_handler())
    kokoro = _make_kokoro(kokoro_handler)
    monkeypatch.setattr("app.pipeline.measure_duration", lambda audio_path: 60)

    summary = await generate_all(wallabag, kokoro, settings=get_settings())

    assert summary["done"] == 1
    assert captured["voice"] == "am_michael"


async def test_voice_falls_back_to_default_when_db_row_missing(env, monkeypatch):
    with sqlite3.connect(get_db_path()) as conn:
        _insert_staged(conn, [(1, "Article One")])
        conn.execute("DELETE FROM settings WHERE key='voice'")
        conn.commit()

    captured = {}

    def kokoro_handler(request: httpx.Request) -> httpx.Response:
        captured["voice"] = json.loads(request.content)["voice"]
        return httpx.Response(200, content=b"FAKE_MP3")

    wallabag = _make_wallabag(_wallabag_ok_handler())
    kokoro = _make_kokoro(kokoro_handler)
    monkeypatch.setattr("app.pipeline.measure_duration", lambda audio_path: 60)

    summary = await generate_all(wallabag, kokoro, settings=get_settings())

    assert summary["done"] == 1
    assert captured["voice"] == get_settings().KOKORO_DEFAULT_VOICE


async def test_no_staged_episodes_returns_empty_summary(env, monkeypatch):
    wallabag = _make_wallabag(_wallabag_ok_handler())
    kokoro = _make_kokoro(lambda request: httpx.Response(200, content=b"FAKE_MP3"))

    summary = await generate_all(wallabag, kokoro, settings=get_settings())

    assert summary == {"total": 0, "done": 0, "failed": 0, "skipped": 0}