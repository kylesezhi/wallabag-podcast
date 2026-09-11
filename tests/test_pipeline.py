"""Tests for the episode generation pipeline and queue operations.

Focus is state transitions and per-episode error isolation for generate_all(),
plus the add_random / delete_item / archive_item / clear_queue / stats
queue operations. Real clients are built on httpx.MockTransport (Wallabag +
Kokoro) and measure_duration is monkeypatched so no real MP3 files are needed.
"""

import asyncio
import json
import logging
import sqlite3

import httpx
import pytest

from app.config import Settings, get_settings
from app.db import (
    add_processed_article,
    create_podcast,
    get_db_path,
    get_queue_episodes,
    get_staged_episodes,
    init_db,
    reset_failed_to_staged,
)
from app.kokoro import KokoroClient
from app.logging_setup import configure_logging
from app.pipeline import (
    _gap_silence,
    _parse_wallabag_ref,
    add_article,
    add_random,
    archive_item,
    clear_queue,
    delete_item,
    delete_podcast,
    generate_all,
    stats,
)
from app.textclean import build_tts_input, build_tts_input_with_sections, has_section_mark, split_tts_text, strip_section_mark
from app.wallabag import WallabagClient, WallabagError

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
    # Existing tests assert exact final-file bytes and summed chunk durations;
    # the end-of-episode silence gap is enabled explicitly in its own tests.
    monkeypatch.setenv("EPISODE_GAP_SECONDS", "0")
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


def _archive_handler(record: list, fail: bool = False):
    """Wallabag handler that records PATCH archive calls; 500 when failing."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth/v2/token":
            return httpx.Response(200, json=_token_response())
        if request.method == "PATCH" and request.url.path.startswith("/api/entries/"):
            record.append(request)
            if fail:
                return httpx.Response(500, text="boom")
            return httpx.Response(200, json={})
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
    podcast_id: int | None = None,
) -> int:
    """Insert an episode with the given status; return its id.

    When ``podcast_id`` is given it is stored on the episode row; otherwise
    the column stays NULL.
    """
    cur = conn.execute(
        "INSERT INTO episodes (wallabag_id, title, source, url, status, "
        "est_minutes, language, duration_sec, drive_id, created_at, podcast_id) "
        "VALUES (?, ?, ?, ?, ?, ?, 'en', ?, ?, '2026-01-01T00:00:00+00:00', ?)",
        (
            wallabag_id,
            title,
            f"example.com/{wallabag_id}",
            f"https://example.com/{wallabag_id}",
            status,
            est_minutes,
            duration_sec,
            drive_id,
            podcast_id,
        ),
    )
    conn.commit()
    return int(cur.lastrowid)


async def test_happy_path(env, monkeypatch):
    with sqlite3.connect(get_db_path()) as conn:
        _insert_staged(conn, [(1, "Article One"), (2, "Article Two")])

    wallabag = _make_wallabag(_wallabag_ok_handler())
    kokoro = _make_kokoro(lambda request: httpx.Response(200, content=b"FAKE_MP3"))
    monkeypatch.setattr("app.pipeline.measure_duration_seconds", lambda audio_path: 60)

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
    monkeypatch.setattr("app.pipeline.measure_duration_seconds", lambda audio_path: 60)

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
    # Chunk synthesis retries once on failure, so failing an episode requires
    # TWO consecutive error responses (initial attempt + retry).
    responses = [
        httpx.Response(500, text="kokoro exploded"),
        httpx.Response(500, text="kokoro exploded"),
        httpx.Response(200, content=b"FAKE_MP3"),
    ]
    kokoro = _make_kokoro(lambda request: responses.pop(0))
    monkeypatch.setattr("app.pipeline.measure_duration_seconds", lambda audio_path: 60)

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
    monkeypatch.setattr("app.pipeline.measure_duration_seconds", lambda audio_path: 60)

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
    monkeypatch.setattr("app.pipeline.measure_duration_seconds", lambda audio_path: 60)

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
    monkeypatch.setattr("app.pipeline.measure_duration_seconds", lambda audio_path: 60)

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
    monkeypatch.setattr("app.pipeline.measure_duration_seconds", lambda audio_path: 60)

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
    monkeypatch.setattr("app.pipeline.measure_duration_seconds", lambda audio_path: 60)

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
# Chunked synthesis: streamed writes, progress, retry, cleanup
# ---------------------------------------------------------------------------


def _chunk_settings(monkeypatch, max_chars: int):
    """Point generate_all at a tiny chunk limit for multi-chunk tests."""
    monkeypatch.setenv("KOKORO_MAX_CHUNK_CHARS", str(max_chars))
    get_settings.cache_clear()
    return get_settings()


async def test_generate_all_synthesizes_in_chunks(env, monkeypatch):
    content = "<p>" + " ".join(["word"] * 100) + "</p>"
    with sqlite3.connect(get_db_path()) as conn:
        conn.execute(
            "INSERT INTO episodes (wallabag_id, title, source, url, status, "
            "est_minutes, language, created_at) VALUES "
            "(1, 'Article One', 'example.com/1', 'https://example.com/1', "
            "'staged', 5, 'en', '2026-01-01T00:00:00+00:00')"
        )
        # Give the article enough body text via the wallabag handler below.
        conn.commit()

    settings = _chunk_settings(monkeypatch, 60)
    # _entry_payload titles entries "Article {id}"; id=1 here.
    expected_chunks = split_tts_text(
        build_tts_input("Article 1", content), max_chars=60
    )
    assert len(expected_chunks) > 1  # the test really exercises chunking

    payloads = [bytes([65 + i]) * 10 for i in range(len(expected_chunks))]
    speech_calls: list[httpx.Request] = []
    payload_iter = iter(payloads)

    def kokoro_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/audio/speech":
            speech_calls.append(request)
            return httpx.Response(200, content=next(payload_iter))
        return httpx.Response(404)

    wallabag = _make_wallabag(_wallabag_entry_handler(content))
    kokoro = _make_kokoro(kokoro_handler)
    monkeypatch.setattr("app.pipeline.measure_duration_seconds", lambda audio: 7)

    summary = await generate_all(wallabag, kokoro, settings=settings)

    assert summary["done"] == 1

    # One request per chunk, in order, each within the char limit.
    inputs = [json.loads(call.content)["input"] for call in speech_calls]
    assert inputs == expected_chunks
    assert all(len(text) <= 60 for text in inputs)

    # Chunks were appended byte-by-byte to one final MP3; no .part remains.
    audio_file = env / "audio" / "1.mp3"
    assert audio_file.read_bytes() == b"".join(payloads)
    assert not (env / "audio" / "1.mp3.part").exists()

    with sqlite3.connect(get_db_path()) as conn:
        row = conn.execute(
            "SELECT status, duration_sec, progress_done, progress_total "
            "FROM episodes WHERE id=1"
        ).fetchone()
    assert row[0] == "done"
    assert row[1] == 7 * len(expected_chunks)  # summed per-chunk durations
    assert (row[2], row[3]) == (len(expected_chunks), len(expected_chunks))


def _wallabag_entry_handler(content: str):
    """Wallabag handler serving one entry payload with ``content``."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth/v2/token":
            return httpx.Response(200, json=_token_response())
        if request.url.path.startswith("/api/entries/"):
            entry_id = int(request.url.path.split("/")[3].split(".")[0])
            return httpx.Response(200, json=_entry_payload(entry_id, content))
        return httpx.Response(404)

    return handler


# ---------------------------------------------------------------------------
# ID3 chapter markers
# ---------------------------------------------------------------------------


def _sectioned_content() -> str:
    """Article HTML with two section titles, well over MIN_TEXT_CHARS."""
    return (
        "<h2>Section Alpha</h2><p>" + " ".join(["word"] * 30) + "</p>"
        "<h2>Section Beta</h2><p>" + " ".join(["word"] * 30) + "</p>"
    )


async def test_generate_embeds_chapters_with_exact_times(env, monkeypatch):
    """Section titles become ID3 chapters; sentinels never reach Kokoro.

    The intro chapter uses the staged row title (not the article title) and
    each section chapter starts at the cumulative duration of the preceding
    chunks (float-precision measurement).
    """
    content = _sectioned_content()
    with sqlite3.connect(get_db_path()) as conn:
        _insert_staged(conn, [(1, "Row Episode Title")])

    settings = _chunk_settings(monkeypatch, 80)

    # Rebuild the exact TTS text the pipeline will produce (article title
    # "Article 1" from _entry_payload; the row title is the intro chapter).
    expected_tts, titles = build_tts_input_with_sections(
        "Article 1", content, min_chars=settings.MIN_TEXT_CHARS
    )
    expected_chunks = split_tts_text(expected_tts, max_chars=80)
    assert len(expected_chunks) > 2  # the test really exercises chunking
    assert titles == ["Section Alpha", "Section Beta"]

    # Distinct per-chunk durations (1.0s, 2.0s, ...) so start times are exact.
    durs = [float(i + 1) for i in range(len(expected_chunks))]
    duration_iter = iter(durs)

    expected_chapters = [(0, "Row Episode Title")]
    cumulative = 0.0
    title_index = 0
    for index, chunk in enumerate(expected_chunks):
        if has_section_mark(chunk):
            expected_chapters.append(
                (int(round(cumulative * 1000)), titles[title_index])
            )
            title_index += 1
        cumulative += durs[index]

    calls: list = []

    def fake_writer(path, chapters, total_ms):
        calls.append((path, list(chapters), total_ms))
        return True

    monkeypatch.setattr("app.pipeline.write_id3_chapters", fake_writer)
    monkeypatch.setattr(
        "app.pipeline.measure_duration_seconds", lambda audio: next(duration_iter)
    )

    speech_calls: list[httpx.Request] = []

    def kokoro_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/audio/speech":
            speech_calls.append(request)
            return httpx.Response(200, content=b"FAKE_MP3")
        return httpx.Response(404)

    wallabag = _make_wallabag(_wallabag_entry_handler(content))
    kokoro = _make_kokoro(kokoro_handler)

    summary = await generate_all(wallabag, kokoro, settings=settings)

    assert summary["done"] == 1

    # The sentinel is stripped before every synthesis request.
    inputs = [json.loads(call.content)["input"] for call in speech_calls]
    assert inputs == [strip_section_mark(c) for c in expected_chunks]
    assert all(not has_section_mark(text) for text in inputs)

    # Exactly one embed call, against the final mp3, with expected chapters.
    assert len(calls) == 1
    assert calls[0][0] == env / "audio" / "1.mp3"
    assert calls[0][1] == expected_chapters
    assert calls[0][2] == int(sum(durs)) * 1000


async def test_generate_no_sections_skips_embedding(env, monkeypatch):
    content = "<p>" + " ".join(["word"] * 100) + "</p>"
    with sqlite3.connect(get_db_path()) as conn:
        _insert_staged(conn, [(1, "Article One")])

    calls: list = []
    monkeypatch.setattr(
        "app.pipeline.write_id3_chapters",
        lambda *args: calls.append(args) or True,
    )
    monkeypatch.setattr("app.pipeline.measure_duration_seconds", lambda audio: 60)

    wallabag = _make_wallabag(_wallabag_entry_handler(content))
    kokoro = _make_kokoro(lambda request: httpx.Response(200, content=b"FAKE_MP3"))

    summary = await generate_all(wallabag, kokoro, settings=get_settings())

    assert summary["done"] == 1
    assert calls == []


async def test_generate_unparseable_duration_skips_embedding(env, monkeypatch):
    """When no chunk duration is measurable the episode still completes (with
    the est_minutes fallback) but gets no chapters — timings are unknown."""
    content = "<h2>Section</h2><p>" + " ".join(["word"] * 40) + "</p>"
    with sqlite3.connect(get_db_path()) as conn:
        _insert_staged(conn, [(1, "Article One")])

    calls: list = []
    monkeypatch.setattr(
        "app.pipeline.write_id3_chapters",
        lambda *args: calls.append(args) or True,
    )
    monkeypatch.setattr("app.pipeline.measure_duration_seconds", lambda audio: None)

    wallabag = _make_wallabag(_wallabag_entry_handler(content))
    kokoro = _make_kokoro(lambda request: httpx.Response(200, content=b"FAKE_MP3"))

    summary = await generate_all(wallabag, kokoro, settings=get_settings())

    assert summary["done"] == 1
    assert calls == []
    with sqlite3.connect(get_db_path()) as conn:
        row = conn.execute(
            "SELECT status, duration_sec FROM episodes WHERE id=1"
        ).fetchone()
    assert row[0] == "done"
    assert row[1] == 5 * 60  # est_minutes fallback


async def test_generate_all_retries_failed_chunk_once(env, monkeypatch):
    with sqlite3.connect(get_db_path()) as conn:
        _insert_staged(conn, [(1, "Article One")])

    responses = [
        httpx.Response(500, text="transient boom"),  # initial attempt
        httpx.Response(200, content=b"FAKE_MP3"),  # retry succeeds
    ]
    kokoro = _make_kokoro(lambda request: responses.pop(0))
    wallabag = _make_wallabag(_wallabag_ok_handler())
    monkeypatch.setattr("app.pipeline.measure_duration_seconds", lambda audio: 60)

    summary = await generate_all(wallabag, kokoro, settings=get_settings())

    assert summary == {"total": 1, "done": 1, "failed": 0, "skipped": 0}
    assert responses == []  # both attempts were consumed


async def test_generate_all_chunk_failure_cleans_up_part(env, monkeypatch):
    with sqlite3.connect(get_db_path()) as conn:
        _insert_staged(conn, [(1, "Article One")])

    # Both the attempt and its retry fail -> the episode is failed.
    kokoro = _make_kokoro(lambda request: httpx.Response(500, text="boom"))
    wallabag = _make_wallabag(_wallabag_ok_handler())

    summary = await generate_all(wallabag, kokoro, settings=get_settings())

    assert summary == {"total": 1, "done": 0, "failed": 1, "skipped": 0}

    with sqlite3.connect(get_db_path()) as conn:
        episodes = _episode_rows(conn)
        assert episodes[0]["status"] == "failed"
        assert "500" in episodes[0]["error"]
    assert not (env / "audio" / "1.mp3.part").exists()
    assert not (env / "audio" / "1.mp3").exists()


async def test_generate_cancelled_mid_chunks_cleans_up_part(env, monkeypatch):
    content = "<p>" + " ".join(["word"] * 100) + "</p>"
    with sqlite3.connect(get_db_path()) as conn:
        _insert_staged(conn, [(1, "Article One"), (2, "Article Two")])

    settings = _chunk_settings(monkeypatch, 60)

    kokoro_calls = []

    def kokoro_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path != "/v1/audio/speech":
            return httpx.Response(404)
        kokoro_calls.append(request)
        if len(kokoro_calls) == 2:
            # First chunk succeeded; cancel lands on the second chunk's
            # request, leaving a partial .part file behind.
            raise asyncio.CancelledError()
        return httpx.Response(200, content=b"CHUNK_MP3")

    wallabag = _make_wallabag(_wallabag_entry_handler(content))
    kokoro = _make_kokoro(kokoro_handler)
    monkeypatch.setattr("app.pipeline.measure_duration_seconds", lambda audio: 60)

    summary = await generate_all(wallabag, kokoro, settings=settings)

    assert summary == {"total": 2, "done": 0, "failed": 1, "skipped": 0}

    with sqlite3.connect(get_db_path()) as conn:
        episodes = _episode_rows(conn)
        assert episodes[0]["status"] == "failed"
        assert episodes[0]["error"] == "Cancelled by user"
        assert episodes[1]["status"] == "staged"

    # The partial file was cleaned up and no final mp3 appeared.
    assert not (env / "audio" / "1.mp3.part").exists()
    assert not (env / "audio" / "1.mp3").exists()
    assert len(kokoro_calls) == 2  # the run halted after the cancel


# ---------------------------------------------------------------------------
# End-of-episode silence gap
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("gap_setting", "copies"), [("0", 0), ("2", 2), ("2.9", 2)])
async def test_generate_appends_trailing_gap(
    env, monkeypatch, gap_setting, copies
):
    """floor(EPISODE_GAP_SECONDS) silence copies end each episode mp3.

    The packaged silent-MP3 asset is appended after the final chunk and its
    measured duration joins the summed chunk durations; ``0`` keeps the output
    byte-identical to a gap-free run.
    """
    asset = _gap_silence()
    assert asset is not None  # the packaged asset must load in tests too
    gap_bytes, gap_duration = asset

    monkeypatch.setenv("EPISODE_GAP_SECONDS", gap_setting)
    get_settings.cache_clear()

    with sqlite3.connect(get_db_path()) as conn:
        _insert_staged(conn, [(1, "Article One")])

    wallabag = _make_wallabag(_wallabag_ok_handler())
    kokoro = _make_kokoro(lambda request: httpx.Response(200, content=b"FAKE_MP3"))
    monkeypatch.setattr("app.pipeline.measure_duration_seconds", lambda audio_path: 60)

    summary = await generate_all(wallabag, kokoro, settings=get_settings())

    assert summary == {"total": 1, "done": 1, "failed": 0, "skipped": 0}

    audio_file = env / "audio" / "1.mp3"
    assert audio_file.read_bytes() == b"FAKE_MP3" + gap_bytes * copies
    assert not (env / "audio" / "1.mp3.part").exists()

    with sqlite3.connect(get_db_path()) as conn:
        row = conn.execute(
            "SELECT status, duration_sec FROM episodes WHERE id=1"
        ).fetchone()
    assert row[0] == "done"
    # Patched per-chunk measurement (60) plus the real measured gap per copy.
    assert row[1] == 60 + gap_duration * copies


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
# Queue operations: add_article (stage a single article by URL or id)
# ---------------------------------------------------------------------------


def _single_entry_handler(entry_id: int, **payload_overrides):
    """MockTransport handler serving /api/entries/{id}.json with one entry."""
    item = _entry_payload(entry_id, _long_content())
    item.update(payload_overrides)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth/v2/token":
            return httpx.Response(200, json=_token_response())
        if request.url.path == f"/api/entries/{entry_id}.json":
            return httpx.Response(200, json=item)
        return httpx.Response(404)

    return handler


@pytest.mark.parametrize(
    "ref",
    [
        "2793",
        "  2793  ",
        "http://192.168.42.223:8000/view/2793",
        "http://192.168.42.223:8000/view/2793/",
        "https://wallabag.example.test/view/2793?search=foo#frag",
    ],
)
def test_parse_wallabag_ref_accepts(ref):
    assert _parse_wallabag_ref(ref) == 2793


@pytest.mark.parametrize(
    "ref",
    ["", "   ", "abc", "view/2793", "/view/", "/view/abc", "http://x/view/2793x"],
)
def test_parse_wallabag_ref_rejects(ref):
    with pytest.raises(ValueError, match="Not a Wallabag article URL"):
        _parse_wallabag_ref(ref)


async def test_add_article_stages_from_url(env):
    with sqlite3.connect(get_db_path()) as conn:
        podcast_id = create_podcast(conn)["id"]
    wallabag = _make_wallabag(_single_entry_handler(2793, title="Deep Dive"))

    title = await add_article(
        "http://192.168.42.223:8000/view/2793",
        wallabag,
        settings=get_settings(),
        podcast_id=podcast_id,
    )

    assert title == "Deep Dive"
    with sqlite3.connect(get_db_path()) as conn:
        row = conn.execute(
            "SELECT title, source, url, est_minutes, language, status, podcast_id "
            "FROM episodes WHERE wallabag_id=2793"
        ).fetchone()
    assert row == ("Deep Dive", "example.com", "https://example.com/2793", 5, "en", "staged", podcast_id)


async def test_add_article_stages_from_bare_id(env):
    wallabag = _make_wallabag(_single_entry_handler(42))

    title = await add_article("42", wallabag, settings=get_settings())

    assert title == "Article 42"
    with sqlite3.connect(get_db_path()) as conn:
        row = conn.execute("SELECT wallabag_id, podcast_id FROM episodes").fetchone()
    assert row == (42, None)


async def test_add_article_allows_archived(env):
    wallabag = _make_wallabag(_single_entry_handler(7, is_archived=1))

    title = await add_article("7", wallabag, settings=get_settings())

    assert title == "Article 7"
    with sqlite3.connect(get_db_path()) as conn:
        assert conn.execute("SELECT 1 FROM episodes WHERE wallabag_id=7").fetchone()


async def test_add_article_bypasses_exclude_tags(env):
    wallabag = _make_wallabag(
        _single_entry_handler(7, tags=[{"id": 1, "slug": "computer", "title": "Computer"}])
    )

    await add_article("7", wallabag, settings=_settings(EXCLUDE_TAGS=["computer"]))

    with sqlite3.connect(get_db_path()) as conn:
        assert conn.execute("SELECT 1 FROM episodes WHERE wallabag_id=7").fetchone()


async def test_add_article_rejects_same_podcast_duplicate(env):
    with sqlite3.connect(get_db_path()) as conn:
        podcast_id = create_podcast(conn)["id"]
        _insert_episode(conn, 7, "Article Seven", podcast_id=podcast_id)
    wallabag = _make_wallabag(_single_entry_handler(7))

    with pytest.raises(ValueError, match="already in this podcast"):
        await add_article("7", wallabag, settings=get_settings(), podcast_id=podcast_id)


async def test_add_article_rejects_other_podcast_duplicate(env):
    with sqlite3.connect(get_db_path()) as conn:
        other_id = create_podcast(conn)["id"]
        target_id = create_podcast(conn)["id"]
        _insert_episode(conn, 7, "Article Seven", podcast_id=other_id)
    wallabag = _make_wallabag(_single_entry_handler(7))

    with pytest.raises(ValueError, match="already staged in another podcast"):
        await add_article("7", wallabag, settings=get_settings(), podcast_id=target_id)


async def test_add_article_rejects_already_generated(env):
    with sqlite3.connect(get_db_path()) as conn:
        add_processed_article(conn, 7, 999)
    wallabag = _make_wallabag(_single_entry_handler(7))

    with pytest.raises(ValueError, match="already generated"):
        await add_article("7", wallabag, settings=get_settings())


async def test_add_article_not_found_raises(env):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth/v2/token":
            return httpx.Response(200, json=_token_response())
        return httpx.Response(404)

    wallabag = _make_wallabag(handler)

    with pytest.raises(WallabagError, match="Article 404 not found"):
        await add_article("http://x/view/404", wallabag, settings=get_settings())


# ---------------------------------------------------------------------------
# Queue operations: delete_item (local cleanup, no Wallabag) / archive_item
# (Wallabag mark-read, no local deletion)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("status", ["staged", "failed", "generating", "done"])
def test_delete_item_deletes_row_for_each_status(env, status):
    with sqlite3.connect(get_db_path()) as conn:
        episode_id = _insert_episode(conn, 7, "Article Seven", status=status)

    delete_item(episode_id)

    with sqlite3.connect(get_db_path()) as conn:
        row = conn.execute("SELECT id FROM episodes WHERE id=?", (episode_id,)).fetchone()
        assert row is None


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


@pytest.mark.parametrize("status", ["staged", "failed", "generating", "done"])
async def test_archive_item_archives_wallabag_entry(env, status):
    record = []
    wallabag = _make_wallabag(_archive_handler(record))
    with sqlite3.connect(get_db_path()) as conn:
        episode_id = _insert_episode(conn, 7, "Article Seven", status=status)

    await archive_item(episode_id, wallabag)

    assert len(record) == 1
    request = record[0]
    assert request.method == "PATCH"
    assert request.url.path == "/api/entries/7.json"
    assert "archive=1" in request.content.decode()

    # Archive is Wallabag-only: the episode row stays, unchanged.
    with sqlite3.connect(get_db_path()) as conn:
        row = conn.execute(
            "SELECT status FROM episodes WHERE id=?", (episode_id,)
        ).fetchone()
        assert row is not None
        assert row[0] == status


async def test_archive_item_wallabag_error_leaves_episode_intact(env):
    """A failing Wallabag archive leaves the queue completely untouched."""
    record = []
    wallabag = _make_wallabag(_archive_handler(record, fail=True))
    with sqlite3.connect(get_db_path()) as conn:
        episode_id = _insert_episode(
            conn, 5, "Article Five", status="done", duration_sec=60, drive_id=1
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
            (5, episode_id),
        )
        conn.commit()

    with pytest.raises(WallabagError):
        await archive_item(episode_id, wallabag)

    # The archive attempt happened, but nothing was changed locally.
    assert len(record) == 1
    with sqlite3.connect(get_db_path()) as conn:
        row = conn.execute(
            "SELECT status, audio_path FROM episodes WHERE id=?", (episode_id,)
        ).fetchone()
        assert row is not None
        assert row[0] == "done"
        processed = conn.execute(
            "SELECT 1 FROM processed_articles WHERE wallabag_id=5"
        ).fetchone()
    assert processed is not None
    assert audio_path.exists() is True


async def test_archive_item_missing_raises(env):
    record = []
    wallabag = _make_wallabag(_archive_handler(record))

    with pytest.raises(ValueError, match="not found"):
        await archive_item(999, wallabag)

    # Nothing was archived: the ValueError fires before any API call.
    assert record == []


async def test_archive_item_archived_raises(env):
    record = []
    wallabag = _make_wallabag(_archive_handler(record))
    with sqlite3.connect(get_db_path()) as conn:
        episode_id = _insert_episode(conn, 1, "Article One", status="archived")

    with pytest.raises(ValueError, match="cannot archive"):
        await archive_item(episode_id, wallabag)

    with sqlite3.connect(get_db_path()) as conn:
        row = conn.execute(
            "SELECT status FROM episodes WHERE id=?", (episode_id,)
        ).fetchone()
    assert row is not None
    assert row[0] == "archived"
    # Legacy archived rows are never re-archived via the API.
    assert record == []


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


# ---------------------------------------------------------------------------
# Retry: reset_failed_to_staged
# ---------------------------------------------------------------------------


def test_reset_failed_to_staged(env):
    with sqlite3.connect(get_db_path()) as conn:
        _insert_episode(conn, 1, "Article One", status="failed")
        _insert_episode(conn, 2, "Article Two", status="failed")
        _insert_episode(conn, 3, "Article Three", status="staged")
        _insert_episode(conn, 4, "Article Four", status="done", duration_sec=60, drive_id=1)
        _insert_episode(conn, 5, "Article Five", status="generating")
        failed_ids = [
            conn.execute(
                "SELECT id FROM episodes WHERE wallabag_id=?", (wid,)
            ).fetchone()[0]
            for wid in (1, 2)
        ]
        for episode_id in failed_ids:
            conn.execute(
                "UPDATE episodes SET error='boom' WHERE id=?", (episode_id,)
            )
        conn.commit()

    with sqlite3.connect(get_db_path()) as conn:
        assert reset_failed_to_staged(conn) == 2

    conn = sqlite3.connect(get_db_path())
    rows = dict(
        conn.execute("SELECT wallabag_id, status FROM episodes").fetchall()
    )
    errors = dict(
        conn.execute(
            "SELECT wallabag_id, error FROM episodes WHERE error IS NOT NULL"
        ).fetchall()
    )
    conn.close()
    assert rows == {1: "staged", 2: "staged", 3: "staged", 4: "done", 5: "generating"}
    assert errors == {}


async def test_generate_all_retries_failed_after_reset(env, monkeypatch):
    """A failed episode swept back to staged is generated like any other."""
    with sqlite3.connect(get_db_path()) as conn:
        _insert_episode(conn, 1, "Article One", status="failed")
        conn.execute("UPDATE episodes SET error='old failure' WHERE wallabag_id=1")
        conn.commit()
        reset_failed_to_staged(conn)

    wallabag = _make_wallabag(_wallabag_ok_handler())
    kokoro = _make_kokoro(lambda request: httpx.Response(200, content=b"FAKE_MP3"))
    monkeypatch.setattr("app.pipeline.measure_duration_seconds", lambda audio_path: 60)

    summary = await generate_all(wallabag, kokoro, settings=get_settings())

    assert summary == {"total": 1, "done": 1, "failed": 0, "skipped": 0}
    with sqlite3.connect(get_db_path()) as conn:
        episodes = get_queue_episodes(conn)
        processed = conn.execute(
            "SELECT 1 FROM processed_articles WHERE wallabag_id=1"
        ).fetchone()
    assert episodes[0]["status"] == "done"
    assert episodes[0]["error"] is None
    assert processed is not None


async def test_generate_all_skips_episode_removed_midrun(env, monkeypatch):
    """A staged episode deleted mid-run must never be generated.

    Removing a still-staged article while an earlier one synthesizes deletes
    its DB row; when the run's snapshot reaches it, generate_all skips it:
    no TTS call, no audio file, no processed_articles row, not counted as
    done/failed.
    """
    with sqlite3.connect(get_db_path()) as conn:
        _insert_staged(conn, [(1, "Article One"), (2, "Article Two")])
        removed_id = conn.execute(
            "SELECT id FROM episodes WHERE wallabag_id=2"
        ).fetchone()[0]

    def wallabag_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth/v2/token":
            return httpx.Response(200, json=_token_response())
        if request.url.path.startswith("/api/entries/"):
            entry_id = int(request.url.path.split("/")[3].split(".")[0])
            if entry_id == 1:
                # Simulate the user removing staged episode 2 mid-run.
                with sqlite3.connect(get_db_path()) as del_conn:
                    del_conn.execute(
                        "DELETE FROM episodes WHERE id=?", (removed_id,)
                    )
                    del_conn.commit()
            return httpx.Response(200, json=_entry_payload(entry_id, _long_content()))
        return httpx.Response(404)

    kokoro_calls: list[httpx.Request] = []

    def kokoro_handler(request: httpx.Request) -> httpx.Response:
        kokoro_calls.append(request)
        return httpx.Response(200, content=b"FAKE_MP3")

    wallabag = _make_wallabag(wallabag_handler)
    kokoro = _make_kokoro(kokoro_handler)
    monkeypatch.setattr("app.pipeline.measure_duration_seconds", lambda audio_path: 60)

    summary = await generate_all(wallabag, kokoro, settings=get_settings())

    assert summary == {"total": 1, "done": 1, "failed": 0, "skipped": 0}
    # Only the surviving episode was synthesized.
    assert len(kokoro_calls) == 1
    assert b"Article 1" in kokoro_calls[0].content
    # The removed episode left no trace: no audio file, no row, no dedupe entry.
    assert not (env / "audio" / f"{removed_id}.mp3").exists()
    with sqlite3.connect(get_db_path()) as conn:
        rows = [
            tuple(row)
            for row in conn.execute(
                "SELECT wallabag_id, status FROM episodes ORDER BY wallabag_id"
            )
        ]
        processed = _processed_ids(conn)
    assert rows == [(1, "done")]
    assert processed == [1]


async def test_generate_all_applies_pronunciations(env, monkeypatch):
    """Configured pronunciations are rewritten before text reaches Kokoro."""
    monkeypatch.setenv("PRONUNCIATIONS", "JSON=Jason")
    get_settings.cache_clear()

    def wallabag_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth/v2/token":
            return httpx.Response(200, json=_token_response())
        if request.url.path.startswith("/api/entries/"):
            entry_id = int(request.url.path.split("/")[3].split(".")[0])
            content = "<p>" + ("Working with JSON data daily here. " * 10) + "</p>"
            return httpx.Response(
                200,
                json=_entry_payload(entry_id, content, title="The JSON guide"),
            )
        return httpx.Response(404)

    kokoro_calls: list[httpx.Request] = []

    def kokoro_handler(request: httpx.Request) -> httpx.Response:
        kokoro_calls.append(request)
        return httpx.Response(200, content=b"FAKE_MP3")

    with sqlite3.connect(get_db_path()) as conn:
        _insert_staged(conn, [(1, "The JSON guide")])

    wallabag = _make_wallabag(wallabag_handler)
    kokoro = _make_kokoro(kokoro_handler)
    monkeypatch.setattr("app.pipeline.measure_duration_seconds", lambda audio_path: 60)

    try:
        summary = await generate_all(wallabag, kokoro, settings=get_settings())
        sent = kokoro_calls[0].content.decode()
    finally:
        get_settings.cache_clear()

    assert summary == {"total": 1, "done": 1, "failed": 0, "skipped": 0}
    assert len(kokoro_calls) == 1
    assert "Jason" in sent
    assert "JSON" not in sent


# ---------------------------------------------------------------------------
# Logging + error traceability (see backend-logging spec)
# ---------------------------------------------------------------------------


@pytest.fixture
def logging_restore():
    """Save and restore the root logger config around logging_setup tests.

    configure_logging() mutates the global root logger; without this, handlers
    pointing at a torn-down tmp_path would leak into later tests. Restores the
    prior handlers + level (closing any handlers configure_logging installed).
    """
    root = logging.getLogger()
    saved_handlers = list(root.handlers)
    saved_level = root.level
    yield
    for handler in list(root.handlers):
        root.removeHandler(handler)
        try:
            handler.close()
        except Exception:
            pass
    for handler in saved_handlers:
        root.addHandler(handler)
    root.setLevel(saved_level)


def _flush_root_handlers() -> None:
    for handler in logging.getLogger().handlers:
        handler.flush()


def test_configure_logging_creates_log_file(env, logging_restore):
    configure_logging(get_settings())
    log_file = env / "logs" / "wallabag-podcast.log"
    assert log_file.is_file()

    logging.getLogger("app.test").info("hello from test")
    _flush_root_handlers()

    assert "hello from test" in log_file.read_text()


def test_configure_logging_respects_log_level(env, logging_restore):
    settings = _settings(DATA_DIR=str(env), LOG_LEVEL="WARNING")
    configure_logging(settings)
    log_file = env / "logs" / "wallabag-podcast.log"

    logging.getLogger("app.test").info("info should be filtered")
    logging.getLogger("app.test").warning("warning should pass")
    _flush_root_handlers()

    text = log_file.read_text()
    assert "info should be filtered" not in text
    assert "warning should pass" in text


async def test_unexpected_failure_stores_type_and_writes_traceback(
    env, monkeypatch, logging_restore
):
    configure_logging(get_settings())
    with sqlite3.connect(get_db_path()) as conn:
        _insert_staged(conn, [(1, "Article One")])

    def boom(*args, **kwargs):
        raise ValueError("kaboom")

    # An unexpected (non-Kokoro/Wallabag) failure mid-pipeline: the short
    # reason goes in episodes.error; the full traceback goes to the log file.
    monkeypatch.setattr("app.pipeline.build_tts_input_from_article_with_sections", boom)

    wallabag = _make_wallabag(_wallabag_ok_handler())
    kokoro = _make_kokoro(lambda request: httpx.Response(200, content=b"FAKE_MP3"))

    summary = await generate_all(wallabag, kokoro, settings=get_settings())

    assert summary == {"total": 1, "done": 0, "failed": 1, "skipped": 0}

    log_file = env / "logs" / "wallabag-podcast.log"
    with sqlite3.connect(get_db_path()) as conn:
        episodes = _episode_rows(conn)
        assert episodes[0]["status"] == "failed"
        assert "ValueError" in episodes[0]["error"]
        assert "kaboom" in episodes[0]["error"]

    text = log_file.read_text()
    assert "Traceback" in text
    assert "ValueError" in text
    assert "kaboom" in text


async def test_lifecycle_logs_on_success(env, monkeypatch, logging_restore):
    configure_logging(get_settings())
    with sqlite3.connect(get_db_path()) as conn:
        _insert_staged(conn, [(1, "Article One")])

    wallabag = _make_wallabag(_wallabag_ok_handler())
    kokoro = _make_kokoro(lambda request: httpx.Response(200, content=b"FAKE_MP3"))
    monkeypatch.setattr("app.pipeline.measure_duration_seconds", lambda audio_path: 60)

    summary = await generate_all(wallabag, kokoro, settings=get_settings())
    assert summary == {"total": 1, "done": 1, "failed": 0, "skipped": 0}

    text = (env / "logs" / "wallabag-podcast.log").read_text()
    assert "Generation run started: 1 staged episodes" in text
    assert "Generating episode 1 (wallabag=1, 1 chunks)" in text
    assert "Episode 1 done: 60s audio, 1 chunks" in text


# ---------------------------------------------------------------------------
# Podcast scoping: add_random / generate_all / stats / clear_queue / delete
# ---------------------------------------------------------------------------


async def test_add_random_stages_into_podcast(env, monkeypatch):
    monkeypatch.setattr("app.pipeline.random.sample", lambda pop, k: pop[:k])
    with sqlite3.connect(get_db_path()) as conn:
        podcast = create_podcast(conn)
    wallabag = _make_wallabag(_metadata_handler([_meta_item(i) for i in range(1, 6)]))

    count = await add_random(
        3, wallabag, settings=get_settings(), podcast_id=podcast["id"]
    )

    assert count == 3
    with sqlite3.connect(get_db_path()) as conn:
        staged = get_staged_episodes(conn, podcast_id=podcast["id"])
        assert [e["wallabag_id"] for e in staged] == [1, 2, 3]
        rows = conn.execute(
            "SELECT podcast_id FROM episodes WHERE status='staged' ORDER BY id"
        ).fetchall()
        assert all(row[0] == podcast["id"] for row in rows)


async def test_add_random_no_repeat_across_podcasts(env, monkeypatch):
    """The global no-repeat invariant spans podcasts: articles staged into one
    podcast are never re-staged into another."""
    monkeypatch.setattr("app.pipeline.random.sample", lambda pop, k: pop[:k])
    with sqlite3.connect(get_db_path()) as conn:
        podcast_a = create_podcast(conn)
        podcast_b = create_podcast(conn)
    wallabag = _make_wallabag(_metadata_handler([_meta_item(i) for i in range(1, 6)]))

    first = await add_random(
        3, wallabag, settings=get_settings(), podcast_id=podcast_a["id"]
    )
    second = await add_random(
        3, wallabag, settings=get_settings(), podcast_id=podcast_b["id"]
    )

    assert first == 3
    assert second == 2
    with sqlite3.connect(get_db_path()) as conn:
        a_staged = get_staged_episodes(conn, podcast_id=podcast_a["id"])
        b_staged = get_staged_episodes(conn, podcast_id=podcast_b["id"])
        assert [e["wallabag_id"] for e in a_staged] == [1, 2, 3]
        assert [e["wallabag_id"] for e in b_staged] == [4, 5]


async def test_generate_all_scoped_to_podcast(env, monkeypatch):
    with sqlite3.connect(get_db_path()) as conn:
        podcast_a = create_podcast(conn)
        podcast_b = create_podcast(conn)
        _insert_episode(
            conn, 1, "Article One", status="staged", podcast_id=podcast_a["id"]
        )
        _insert_episode(
            conn, 2, "Article Two", status="staged", podcast_id=podcast_a["id"]
        )
        b_id = _insert_episode(
            conn, 3, "Article Three", status="staged", podcast_id=podcast_b["id"]
        )

    wallabag = _make_wallabag(_wallabag_ok_handler())
    kokoro = _make_kokoro(lambda request: httpx.Response(200, content=b"FAKE_MP3"))
    monkeypatch.setattr("app.pipeline.measure_duration_seconds", lambda audio_path: 60)

    summary = await generate_all(
        wallabag, kokoro, settings=get_settings(), podcast_id=podcast_a["id"]
    )

    assert summary == {"total": 2, "done": 2, "failed": 0, "skipped": 0}
    with sqlite3.connect(get_db_path()) as conn:
        a_rows = conn.execute(
            "SELECT status FROM episodes WHERE podcast_id=?", (podcast_a["id"],)
        ).fetchall()
        b_rows = conn.execute(
            "SELECT status FROM episodes WHERE podcast_id=?", (podcast_b["id"],)
        ).fetchall()
        assert [row[0] for row in a_rows] == ["done", "done"]
        assert [row[0] for row in b_rows] == ["staged"]
    assert not (env / "audio" / f"{b_id}.mp3").exists()


def test_stats_scoped(env):
    with sqlite3.connect(get_db_path()) as conn:
        podcast_a = create_podcast(conn)
        podcast_b = create_podcast(conn)
        _insert_episode(
            conn, 1, "Article One", status="staged", est_minutes=5,
            podcast_id=podcast_a["id"],
        )
        _insert_episode(
            conn, 2, "Article Two", status="staged", est_minutes=10,
            podcast_id=podcast_a["id"],
        )
        _insert_episode(
            conn, 3, "Article Three", status="done", duration_sec=120,
            drive_id=7, podcast_id=podcast_a["id"],
        )
        _insert_episode(
            conn, 4, "Article Four", status="done", duration_sec=60,
            drive_id=8, podcast_id=podcast_b["id"],
        )

    assert stats(podcast_id=podcast_a["id"]) == {
        "total_minutes": 17,
        "articles": 3,
        "staged": 2,
        "done": 1,
        "failed": 0,
        "generating": 0,
        "archived": 0,
        "drive_id": 7,
    }
    assert stats() == {
        "total_minutes": 18,
        "articles": 4,
        "staged": 2,
        "done": 2,
        "failed": 0,
        "generating": 0,
        "archived": 0,
        "drive_id": 8,
    }


def test_clear_queue_scoped(env):
    with sqlite3.connect(get_db_path()) as conn:
        podcast_a = create_podcast(conn)
        podcast_b = create_podcast(conn)
        a_done = _insert_episode(
            conn, 3, "Article Three", status="done", duration_sec=60,
            drive_id=1, podcast_id=podcast_a["id"],
        )
        _insert_episode(
            conn, 1, "Article One", status="staged", podcast_id=podcast_a["id"]
        )
        _insert_episode(
            conn, 2, "Article Two", status="failed", podcast_id=podcast_a["id"]
        )
        b_staged = _insert_episode(
            conn, 4, "Article Four", status="staged", podcast_id=podcast_b["id"]
        )

    assert clear_queue(podcast_id=podcast_a["id"]) == 2

    with sqlite3.connect(get_db_path()) as conn:
        rows = conn.execute("SELECT id, status FROM episodes ORDER BY id").fetchall()
        assert [(row[0], row[1]) for row in rows] == [
            (a_done, "done"),
            (b_staged, "staged"),
        ]


def test_delete_podcast_orchestration(env):
    """delete_podcast removes a podcast's rows, dedupe entries, audio files,
    and stray .part files while leaving other podcasts untouched."""
    with sqlite3.connect(get_db_path()) as conn:
        podcast_a = create_podcast(conn)
        podcast_b = create_podcast(conn)
        done_1 = _insert_episode(
            conn, 1, "Article One", status="done", duration_sec=60,
            drive_id=1, podcast_id=podcast_a["id"],
        )
        done_2 = _insert_episode(
            conn, 2, "Article Two", status="done", duration_sec=60,
            drive_id=1, podcast_id=podcast_a["id"],
        )
        _insert_episode(
            conn, 3, "Article Three", status="staged", podcast_id=podcast_a["id"]
        )
        done_4 = _insert_episode(
            conn, 4, "Article Four", status="done", duration_sec=60,
            drive_id=2, podcast_id=podcast_b["id"],
        )
        audio_dir = env / "audio"
        audio_dir.mkdir(parents=True, exist_ok=True)
        files = {
            "mp3_1": audio_dir / f"{done_1}.mp3",
            "mp3_2": audio_dir / f"{done_2}.mp3",
            "part_1": audio_dir / f"{done_1}.mp3.part",
            "mp3_b": audio_dir / f"{done_4}.mp3",
        }
        for path in files.values():
            path.write_bytes(b"FAKE_MP3")
        conn.execute(
            "UPDATE episodes SET audio_path=? WHERE id=?", (str(files["mp3_1"]), done_1)
        )
        conn.execute(
            "UPDATE episodes SET audio_path=? WHERE id=?", (str(files["mp3_2"]), done_2)
        )
        conn.execute(
            "UPDATE episodes SET audio_path=? WHERE id=?", (str(files["mp3_b"]), done_4)
        )
        add_processed_article(conn, 1, done_1)
        add_processed_article(conn, 2, done_2)
        add_processed_article(conn, 4, done_4)
        conn.commit()

    result = delete_podcast(podcast_a["id"])

    assert result == {
        "name": podcast_a["name"],
        "guid": podcast_a["guid"],
        "episode_count": 3,
    }
    assert not files["mp3_1"].exists()
    assert not files["mp3_2"].exists()
    assert not files["part_1"].exists()
    assert files["mp3_b"].exists()

    with sqlite3.connect(get_db_path()) as conn:
        remaining = conn.execute(
            "SELECT id, status FROM episodes ORDER BY id"
        ).fetchall()
        assert [(row[0], row[1]) for row in remaining] == [(done_4, "done")]
        assert _processed_ids(conn) == [4]
        assert (
            conn.execute("SELECT 1 FROM podcasts WHERE id=?", (podcast_a["id"],)).fetchone()
            is None
        )
        assert (
            conn.execute("SELECT 1 FROM podcasts WHERE id=?", (podcast_b["id"],)).fetchone()
            is not None
        )

    with pytest.raises(ValueError, match="not found"):
        delete_podcast(999)
