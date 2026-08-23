"""Tests for the episode generation pipeline and queue operations.

Focus is state transitions and per-episode error isolation for generate_all(),
plus the add_random / delete_item / clear_queue / stats
queue operations. Real clients are built on httpx.MockTransport (Wallabag +
Kokoro) and measure_duration is monkeypatched so no real MP3 files are needed.
"""

import asyncio
import json
import sqlite3

import httpx
import pytest

from app.config import Settings, get_settings
from app.db import add_processed_article, get_db_path, init_db
from app.kokoro import KokoroClient
from app.pipeline import (
    add_random,
    clear_queue,
    delete_item,
    generate_all,
    stats,
)
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


def _meta_item(
    entry_id: int,
    title: str | None = None,
    tags: list[str] | None = None,
    reading_time: int = 5,
) -> dict:
    """A Wallabag metadata payload item (as returned by list_unread_metadata)."""
    return {
        "id": entry_id,
        "title": title or f"Article {entry_id}",
        "url": f"https://example.com/{entry_id}",
        "domain_name": "example.com",
        "reading_time": reading_time,
        "language": "en",
        "tags": tags or [],
        "is_archived": 0,
        "is_starred": 0,
    }


def _metadata_handler(items: list[dict]):
    """MockTransport handler serving the entries.json metadata endpoint."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth/v2/token":
            return httpx.Response(200, json=_token_response())
        if request.url.path == "/api/entries.json":
            return httpx.Response(200, json={"_embedded": {"items": items}, "pages": 1})
        return httpx.Response(404)

    return handler


def _insert_episode(
    conn: sqlite3.Connection,
    wallabag_id: int,
    title: str,
    status: str = "staged",
    est_minutes: int = 5,
    duration_sec: int | None = None,
    drive_id: int | None = None,
) -> int:
    """Insert an episode with the given status; return its id."""
    cur = conn.execute(
        "INSERT INTO episodes (wallabag_id, title, source, url, status, "
        "est_minutes, language, duration_sec, drive_id, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, 'en', ?, ?, '2026-01-01T00:00:00+00:00')",
        (
            wallabag_id,
            title,
            f"example.com/{wallabag_id}",
            f"https://example.com/{wallabag_id}",
            status,
            est_minutes,
            duration_sec,
            drive_id,
        ),
    )
    conn.commit()
    return int(cur.lastrowid)


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


# ---------------------------------------------------------------------------
# generate_all cancellation + removable generating episodes
# ---------------------------------------------------------------------------


def test_delete_item_generating(env):
    with sqlite3.connect(get_db_path()) as conn:
        episode_id = _insert_episode(conn, 1, "Article One", status="generating")

    delete_item(episode_id)

    with sqlite3.connect(get_db_path()) as conn:
        row = conn.execute("SELECT id FROM episodes WHERE id=?", (episode_id,)).fetchone()
        assert row is None


async def test_generate_cancelled_mid_flight(env, monkeypatch):
    with sqlite3.connect(get_db_path()) as conn:
        _insert_staged(conn, [(1, "Article One"), (2, "Article Two")])

    wallabag = _make_wallabag(_wallabag_ok_handler())

    kokoro_calls = []

    def kokoro_handler(request: httpx.Request) -> httpx.Response:
        kokoro_calls.append(request)
        if len(kokoro_calls) == 1:
            # Raise from the mock transport: this propagates out of
            # `await kokoro_client.synthesize(...)` (CancelledError is a
            # BaseException, so KokoroClient's except clauses don't catch it).
            raise asyncio.CancelledError()
        return httpx.Response(200, content=b"FAKE_MP3")

    kokoro = _make_kokoro(kokoro_handler)
    monkeypatch.setattr("app.pipeline.measure_duration", lambda audio_path: 60)

    summary = await generate_all(wallabag, kokoro, settings=get_settings())

    # The in-flight episode is marked failed and the run halts: episode 2
    # stays staged and is never fetched/synthesized.
    assert summary == {"total": 2, "done": 0, "failed": 1, "skipped": 0}

    with sqlite3.connect(get_db_path()) as conn:
        episodes = _episode_rows(conn)
        assert episodes[0]["status"] == "failed"
        assert episodes[0]["error"] == "Cancelled by user"
        assert episodes[1]["status"] == "staged"
        assert episodes[1]["error"] is None
        # Cancellation does not touch processed_articles (same rule as other
        # failures): the article can be re-picked/generated later.
        assert _processed_ids(conn) == []

    # No audio written: the cancel landed at the kokoro await, before
    # audio_path.write_bytes was reached.
    assert (env / "audio" / "1.mp3").exists() is False


async def test_generate_cancelled_before_first_await(env, monkeypatch):
    with sqlite3.connect(get_db_path()) as conn:
        _insert_staged(conn, [(1, "Article One"), (2, "Article Two")])

    wallabag = _make_wallabag(_wallabag_ok_handler())
    kokoro = _make_kokoro(lambda request: httpx.Response(200, content=b"FAKE_MP3"))
    monkeypatch.setattr("app.pipeline.measure_duration", lambda audio_path: 60)

    # Gate the first wallabag fetch so the task parks at an await inside the
    # loop, then deliver the cancel externally via task.cancel() — proving a
    # real task cancellation propagates into generate_all.
    entered = asyncio.Event()
    release = asyncio.Event()
    original_get_entry = WallabagClient.get_entry

    async def gated_get_entry(self, entry_id):
        entered.set()
        await release.wait()
        return await original_get_entry(self, entry_id)

    monkeypatch.setattr("app.pipeline.WallabagClient.get_entry", gated_get_entry)

    task = asyncio.create_task(
        generate_all(wallabag, kokoro, settings=get_settings())
    )
    await entered.wait()  # the task is now parked at release.wait()
    task.cancel()
    release.set()  # irrelevant now — the pending cancel wins
    # generate_all swallows CancelledError internally, so awaiting the task
    # returns the partial summary normally (it does not raise).
    summary = await task

    assert summary == {"total": 2, "done": 0, "failed": 1, "skipped": 0}

    with sqlite3.connect(get_db_path()) as conn:
        episodes = _episode_rows(conn)
        assert episodes[0]["status"] == "failed"
        assert episodes[0]["error"] == "Cancelled by user"
        assert episodes[1]["status"] == "staged"
        assert _processed_ids(conn) == []

    assert (env / "audio" / "1.mp3").exists() is False


# ---------------------------------------------------------------------------
# Queue operations: add_random
# ---------------------------------------------------------------------------


def _staged_wallabag_ids(conn: sqlite3.Connection) -> list[int]:
    rows = conn.execute(
        "SELECT wallabag_id FROM episodes WHERE status='staged' ORDER BY wallabag_id"
    ).fetchall()
    return [row[0] for row in rows]


async def test_add_random_basic(env, monkeypatch):
    # Deterministic pick: first k candidates.
    monkeypatch.setattr("app.pipeline.random.sample", lambda pop, k: pop[:k])
    wallabag = _make_wallabag(
        _metadata_handler([_meta_item(i) for i in range(1, 6)])
    )

    count = await add_random(3, wallabag, settings=get_settings())

    assert count == 3
    with sqlite3.connect(get_db_path()) as conn:
        assert _staged_wallabag_ids(conn) == [1, 2, 3]
        rows = conn.execute(
            "SELECT wallabag_id, title, source, url, est_minutes, language "
            "FROM episodes WHERE status='staged' ORDER BY id"
        ).fetchall()
        assert all(r[0] == i + 1 for i, r in enumerate(rows))
        assert all(r[1] == f"Article {r[0]}" for r in rows)
        assert all(r[2] == "example.com" for r in rows)
        assert all(r[3] == f"https://example.com/{r[0]}" for r in rows)
        assert all(r[4] == 5 for r in rows)
        assert all(r[5] == "en" for r in rows)


async def test_add_random_excludes_tagged(env):
    items = [
        _meta_item(1, tags=["computer"]),
        _meta_item(2, tags=["tech", "computer"]),
        _meta_item(3, tags=["tech"]),
        _meta_item(4, tags=[]),
        _meta_item(5, tags=["news"]),
    ]
    wallabag = _make_wallabag(_metadata_handler(items))

    count = await add_random(10, wallabag, settings=_settings(EXCLUDE_TAGS=["computer"]))

    assert count == 3
    with sqlite3.connect(get_db_path()) as conn:
        assert _staged_wallabag_ids(conn) == [3, 4, 5]


async def test_add_random_dedupes_processed_and_staged(env):
    with sqlite3.connect(get_db_path()) as conn:
        add_processed_article(conn, 1, 999)
        _insert_staged(conn, [(2, "Already Staged")])

    wallabag = _make_wallabag(_metadata_handler([_meta_item(i) for i in range(1, 6)]))

    count = await add_random(10, wallabag, settings=get_settings())

    assert count == 3
    with sqlite3.connect(get_db_path()) as conn:
        # id=2 was pre-staged (kept); id=1 excluded (processed); 3,4,5 newly staged
        assert _staged_wallabag_ids(conn) == [2, 3, 4, 5]


async def test_add_random_fewer_candidates_than_n(env):
    wallabag = _make_wallabag(_metadata_handler([_meta_item(1), _meta_item(2)]))

    count = await add_random(10, wallabag, settings=get_settings())

    assert count == 2
    with sqlite3.connect(get_db_path()) as conn:
        assert _staged_wallabag_ids(conn) == [1, 2]


async def test_add_random_uses_random_sample(env, monkeypatch):
    picked = []

    def fake_sample(pop, k):
        picked.append((pop, k))
        return pop[:k]

    monkeypatch.setattr("app.pipeline.random.sample", fake_sample)
    wallabag = _make_wallabag(_metadata_handler([_meta_item(i) for i in range(1, 6)]))

    count = await add_random(3, wallabag, settings=get_settings())

    assert count == 3
    assert len(picked) == 1
    population, k = picked[0]
    assert k == 3
    assert [m.id for m in population] == [1, 2, 3, 4, 5]
    with sqlite3.connect(get_db_path()) as conn:
        assert _staged_wallabag_ids(conn) == [1, 2, 3]


async def test_add_random_idempotent(env, monkeypatch):
    monkeypatch.setattr("app.pipeline.random.sample", lambda pop, k: pop[:k])
    wallabag = _make_wallabag(_metadata_handler([_meta_item(i) for i in range(1, 6)]))

    first = await add_random(3, wallabag, settings=get_settings())
    second = await add_random(3, wallabag, settings=get_settings())

    assert first == 3
    assert second == 2
    with sqlite3.connect(get_db_path()) as conn:
        assert _staged_wallabag_ids(conn) == [1, 2, 3, 4, 5]


# ---------------------------------------------------------------------------
# Queue operations: delete_item
# ---------------------------------------------------------------------------


def test_delete_item_staged(env):
    with sqlite3.connect(get_db_path()) as conn:
        episode_id = _insert_episode(conn, 1, "Article One", status="staged")

    delete_item(episode_id)

    with sqlite3.connect(get_db_path()) as conn:
        row = conn.execute("SELECT id FROM episodes WHERE id=?", (episode_id,)).fetchone()
        assert row is None


def test_delete_item_failed(env):
    with sqlite3.connect(get_db_path()) as conn:
        episode_id = _insert_episode(conn, 1, "Article One", status="failed")

    delete_item(episode_id)

    with sqlite3.connect(get_db_path()) as conn:
        row = conn.execute("SELECT id FROM episodes WHERE id=?", (episode_id,)).fetchone()
        assert row is None


def test_delete_item_done_succeeds(env):
    with sqlite3.connect(get_db_path()) as conn:
        episode_id = _insert_episode(
            conn, 1, "Article One", status="done", duration_sec=60, drive_id=1
        )
        audio_dir = env / "audio"
        audio_dir.mkdir(parents=True, exist_ok=True)
        audio_path = audio_dir / f"{episode_id}.mp3"
        audio_path.write_bytes(b"0123456789" * 10)
        conn.execute(
            "UPDATE episodes SET audio_path=? WHERE id=?",
            (str(audio_path), episode_id),
        )
        conn.execute(
            "INSERT INTO processed_articles (wallabag_id, episode_id, processed_at) "
            "VALUES (?, ?, '2026-01-02T00:00:00+00:00')",
            (1, episode_id),
        )
        conn.commit()

    delete_item(episode_id)

    with sqlite3.connect(get_db_path()) as conn:
        row = conn.execute("SELECT id FROM episodes WHERE id=?", (episode_id,)).fetchone()
        assert row is None
        assert _processed_ids(conn) == []
    assert audio_path.exists() is False


def test_delete_item_done_swallows_missing_mp3(env):
    with sqlite3.connect(get_db_path()) as conn:
        episode_id = _insert_episode(
            conn, 1, "Article One", status="done", duration_sec=60, drive_id=1
        )
        conn.execute(
            "UPDATE episodes SET audio_path=? WHERE id=?",
            (str(env / "audio" / "9999.mp3"), episode_id),
        )
        conn.execute(
            "INSERT INTO processed_articles (wallabag_id, episode_id, processed_at) "
            "VALUES (?, ?, '2026-01-02T00:00:00+00:00')",
            (1, episode_id),
        )
        conn.commit()

    delete_item(episode_id)

    with sqlite3.connect(get_db_path()) as conn:
        row = conn.execute("SELECT id FROM episodes WHERE id=?", (episode_id,)).fetchone()
        assert row is None
        assert _processed_ids(conn) == []


def test_delete_item_missing_raises(env):
    with pytest.raises(ValueError, match="not found"):
        delete_item(999)


def test_delete_item_archived_raises(env):
    with sqlite3.connect(get_db_path()) as conn:
        episode_id = _insert_episode(conn, 1, "Article One", status="archived")

    with pytest.raises(ValueError, match="cannot delete"):
        delete_item(episode_id)

    with sqlite3.connect(get_db_path()) as conn:
        row = conn.execute(
            "SELECT status FROM episodes WHERE id=?", (episode_id,)
        ).fetchone()
    assert row is not None
    assert row[0] == "archived"


# ---------------------------------------------------------------------------
# Queue operations: clear_queue / stats
# ---------------------------------------------------------------------------


def test_clear_queue(env):
    with sqlite3.connect(get_db_path()) as conn:
        _insert_episode(conn, 1, "Article One", status="staged")
        _insert_episode(conn, 2, "Article Two", status="staged")
        _insert_episode(conn, 3, "Article Three", status="failed")
        _insert_episode(conn, 4, "Article Four", status="done", duration_sec=60, drive_id=1)
        _insert_episode(conn, 5, "Article Five", status="done", duration_sec=60, drive_id=1)

    assert clear_queue() == 3

    with sqlite3.connect(get_db_path()) as conn:
        rows = conn.execute("SELECT wallabag_id, status FROM episodes ORDER BY id").fetchall()
        assert [(r[0], r[1]) for r in rows] == [(4, "done"), (5, "done")]


def test_stats(env):
    with sqlite3.connect(get_db_path()) as conn:
        _insert_episode(conn, 1, "Article One", status="staged", est_minutes=5)
        _insert_episode(conn, 2, "Article Two", status="staged", est_minutes=10)
        _insert_episode(
            conn, 3, "Article Three", status="done", est_minutes=5,
            duration_sec=120, drive_id=7,
        )

    assert stats() == {
        "total_minutes": 17,
        "articles": 3,
        "staged": 2,
        "done": 1,
        "failed": 0,
        "generating": 0,
        "archived": 0,
        "drive_id": 7,
    }


def test_stats_empty_queue(env):
    assert stats() == {
        "total_minutes": 0,
        "articles": 0,
        "staged": 0,
        "done": 0,
        "failed": 0,
        "generating": 0,
        "archived": 0,
        "drive_id": None,
    }