"""Tests for the web UI routes in app/main.py.

The env fixture points DATA_DIR at tmp_path and initializes the DB schema,
matching the pattern used in test_pipeline.py and test_rss.py. Routes that
need external clients (Wallabag, Kokoro) use lightweight mock objects whose
methods are async stubs; pipeline functions that run in the background are
patched at the ``app.main`` import site.
"""

import asyncio
import contextlib
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.db import connect, get_db_path, get_setting, init_db
from app.main import _human_duration, app

_REQUIRED_ENV = {
    "WALLABAG_CLIENT_ID": "test_client_id",
    "WALLABAG_CLIENT_SECRET": "test_client_secret",
    "WALLABAG_USERNAME": "test_user",
    "WALLABAG_PASSWORD": "test_pass",
}


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Point DATA_DIR at tmp_path, init the DB schema, and seed settings."""
    for key, value in _REQUIRED_ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("KOKORO_BASE_URL", "http://kokoro.test")
    monkeypatch.setenv("WALLABAG_URL", "https://wallabag.test")
    get_settings.cache_clear()
    init_db(get_db_path())
    return tmp_path


@pytest.fixture
def client(env):
    with TestClient(app) as test_client:
        yield test_client


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


def _insert_done(conn: sqlite3.Connection, wallabag_id: int, title: str) -> None:
    conn.execute(
        "INSERT INTO episodes (wallabag_id, title, source, url, status, "
        "est_minutes, language, audio_path, duration_sec, drive_id, "
        "created_at, generated_at) VALUES (?, ?, ?, ?, 'done', 5, 'en', "
        "'/tmp/audio.mp3', 300, 1, '2026-01-01T00:00:00+00:00', "
        "'2026-01-02T00:00:00+00:00')",
        (
            wallabag_id,
            title,
            f"example.com/{wallabag_id}",
            f"https://example.com/{wallabag_id}",
        ),
    )
    conn.commit()


def _insert_failed(conn: sqlite3.Connection, wallabag_id: int, title: str) -> None:
    conn.execute(
        "INSERT INTO episodes (wallabag_id, title, source, url, status, "
        "est_minutes, language, error, created_at) VALUES (?, ?, ?, ?, "
        "'failed', 5, 'en', 'some error', '2026-01-01T00:00:00+00:00')",
        (
            wallabag_id,
            title,
            f"example.com/{wallabag_id}",
            f"https://example.com/{wallabag_id}",
        ),
    )
    conn.commit()


def _insert_generating(conn: sqlite3.Connection, wallabag_id: int, title: str) -> None:
    conn.execute(
        "INSERT INTO episodes (wallabag_id, title, source, url, status, "
        "est_minutes, language, created_at) VALUES (?, ?, ?, ?, 'generating', "
        "5, 'en', '2026-01-01T00:00:00+00:00')",
        (
            wallabag_id,
            title,
            f"example.com/{wallabag_id}",
            f"https://example.com/{wallabag_id}",
        ),
    )
    conn.commit()


def _write_audio_file(tmp_path: Path, episode_id: int) -> Path:
    """Write a 100-byte fake MP3 blob under ``tmp_path/audio``.

    Returns the file path so the test can point an episode's audio_path at a
    real file on disk (mirrors what pipeline.py does for generated episodes).
    """
    audio_dir = tmp_path / "audio"
    audio_dir.mkdir(exist_ok=True)
    audio_path = audio_dir / f"{episode_id}.mp3"
    audio_path.write_bytes(b"0123456789" * 10)
    return audio_path


def _insert_done_audio(
    conn: sqlite3.Connection, wallabag_id: int, title: str, audio_path: Path
) -> None:
    """Insert a done episode whose audio_path points at a real file."""
    conn.execute(
        "INSERT INTO episodes (wallabag_id, title, source, url, status, "
        "est_minutes, language, audio_path, duration_sec, drive_id, "
        "created_at, generated_at) VALUES (?, ?, ?, ?, 'done', 5, 'en', "
        "?, 300, 1, '2026-01-01T00:00:00+00:00', "
        "'2026-01-02T00:00:00+00:00')",
        (
            wallabag_id,
            title,
            f"example.com/{wallabag_id}",
            f"https://example.com/{wallabag_id}",
            str(audio_path),
        ),
    )
    conn.commit()


class _MockWallabag:
    """Minimal async mock matching WallabagClient's public surface."""

    def __init__(self, connected: bool = True):
        self._connected = connected

    async def test_connection(self) -> bool:
        return self._connected

    async def aclose(self) -> None:
        pass


class _ArchiveSpyWallabag:
    """Fake wallabag client whose archive() records entry ids and can fail."""

    def __init__(self, error: Exception | None = None):
        self.archive_calls: list[int] = []
        self._error = error

    async def archive(self, entry_id: int) -> None:
        self.archive_calls.append(entry_id)
        if self._error is not None:
            raise self._error

    async def aclose(self) -> None:
        pass


class _MockKokoro:
    """Minimal async mock matching KokoroClient's public surface."""

    def __init__(self, voice_list: list[str] | None = None):
        self._voices = voice_list or ["af_heart", "af_blossom"]

    async def voices(self) -> list[str]:
        return self._voices

    async def aclose(self) -> None:
        pass


# ---------------------------------------------------------------------------
# Home page
# ---------------------------------------------------------------------------


def test_home_empty(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "Today" in response.text
    assert "Drive" in response.text
    assert "Add 10 Random Articles" in response.text
    assert "Generate" in response.text
    assert "No articles" in response.text.lower() or "empty" in response.text.lower()


def test_home_shows_queue(client):
    with sqlite3.connect(get_db_path()) as conn:
        _insert_staged(conn, [(1, "First Article"), (2, "Second Article")])

    response = client.get("/")

    assert response.status_code == 200
    assert "First Article" in response.text
    assert "Second Article" in response.text
    assert "staged" in response.text.lower()


def test_home_article_links_to_wallabag(client):
    with sqlite3.connect(get_db_path()) as conn:
        _insert_staged(conn, [(1, "First Article"), (2, "Second Article")])

    response = client.get("/")

    assert response.status_code == 200
    assert 'href="' in response.text and "/view/1" in response.text
    assert 'href="' in response.text and "/view/2" in response.text
    assert 'target="_blank"' in response.text
    assert 'rel="noopener noreferrer"' in response.text


def test_home_shows_done_with_duration(client):
    with sqlite3.connect(get_db_path()) as conn:
        _insert_done(conn, 10, "Finished Episode")

    response = client.get("/")

    assert response.status_code == 200
    assert "Finished Episode" in response.text
    assert "done" in response.text.lower()
    assert "5 minutes" in response.text


@pytest.mark.parametrize(
    ("minutes", "expected"),
    [
        (45, "45 minutes"),
        (60, "1 hour"),
        (61, "1 hour, 1 minute"),
        (125, "2 hours, 5 minutes"),
        (1440, "1 day"),
        (1501, "1 day, 1 hour, 1 minute"),
        (0, "0 minutes"),
        (None, "0 minutes"),
    ],
)
def test_human_duration_filter(minutes, expected):
    assert _human_duration(minutes) == expected


def test_home_humanizes_drive_total(client):
    with sqlite3.connect(get_db_path()) as conn:
        _insert_staged(conn, [(1, "First Article"), (2, "Second Article")])
        conn.execute("UPDATE episodes SET est_minutes = 90 WHERE wallabag_id IN (1, 2)")
        conn.commit()

    response = client.get("/")

    assert response.status_code == 200
    assert "3 hours" in response.text


def test_home_shows_failed_with_error(client):
    with sqlite3.connect(get_db_path()) as conn:
        _insert_failed(conn, 20, "Broken Article")

    response = client.get("/")

    assert response.status_code == 200
    assert "Broken Article" in response.text
    assert "failed" in response.text.lower()
    assert "some error" in response.text


def test_home_failed_only_shows_ready_to_generate(client):
    with sqlite3.connect(get_db_path()) as conn:
        _insert_failed(conn, 20, "Broken Article")

    response = client.get("/")

    assert response.status_code == 200
    assert "Ready to generate" in response.text


def test_home_progress_counts_generating_episode(client):
    app.state.generating = True
    try:
        with sqlite3.connect(get_db_path()) as conn:
            _insert_generating(conn, 100, "Mid Synthesis")
            _insert_staged(
                conn, [(101, "Staged One"), (102, "Staged Two"), (103, "Staged Three"),
                       (104, "Staged Four"), (105, "Staged Five")]
            )
            _insert_done(conn, 106, "Finished Episode")
            _insert_failed(conn, 107, "Broken Article")

        response = client.get("/")

        assert response.status_code == 200
        assert 'id="progress-done">2<' in response.text
        assert 'id="progress-total">8<' in response.text
        assert "episodes done" not in response.text
        # Generate Audio is a non-clickable busy button while a run is active.
        assert (
            '<button type="submit" class="btn btn-primary btn-block" disabled aria-busy="true">'
            in response.text
        )
        assert '<span class="spinner" aria-hidden="true"></span>Generating' in response.text
        assert ">Generate Audio<" not in response.text
    finally:
        app.state.generating = False


def test_home_generate_button_enabled_when_idle(client):
    response = client.get("/")

    assert response.status_code == 200
    assert '<button type="submit" class="btn btn-primary btn-block">Generate Audio</button>' in response.text
    assert 'class="spinner"' not in response.text


# ---------------------------------------------------------------------------
# Settings page
# ---------------------------------------------------------------------------


def test_settings_page(client):
    app.state.wallabag_client = _MockWallabag(connected=True)
    app.state.kokoro_client = _MockKokoro()

    response = client.get("/settings")

    assert response.status_code == 200
    assert "Settings" in response.text
    assert "articles_per_drive" in response.text
    assert "voice" in response.text.lower()
    assert "connected" in response.text.lower()
    assert "Coming soon" in response.text
    # Autosave replaced the Save button: status line only, nothing to submit.
    assert 'id="save-status"' in response.text
    assert "btn-block" not in response.text


def test_settings_page_wallabag_fail(client):
    app.state.wallabag_client = _MockWallabag(connected=False)
    app.state.kokoro_client = _MockKokoro()

    response = client.get("/settings")

    assert response.status_code == 200
    assert "not connected" in response.text.lower()


def test_settings_page_kokoro_unreachable(client):
    class _BrokenKokoro:
        async def voices(self):
            raise Exception("unreachable")

        async def aclose(self) -> None:
            pass

    app.state.wallabag_client = _MockWallabag(connected=True)
    app.state.kokoro_client = _BrokenKokoro()

    response = client.get("/settings")

    assert response.status_code == 200
    assert "unreachable" in response.text
    assert "af_heart" in response.text


# ---------------------------------------------------------------------------
# Queue actions
# ---------------------------------------------------------------------------


def test_add_random_success(client, monkeypatch):
    async def mock_add_random(n, wallabag_client, settings):
        conn = connect()
        try:
            conn.execute(
                "INSERT INTO episodes (wallabag_id, title, source, url, status, "
                "est_minutes, language, created_at) VALUES (?, ?, ?, ?, 'staged', "
                "5, 'en', '2026-01-01T00:00:00+00:00')",
                (999, "Mocked Article", "example.com", "https://example.com"),
            )
            conn.commit()
        finally:
            conn.close()
        return 1

    monkeypatch.setattr("app.main.add_random", mock_add_random)

    response = client.post("/queue/add-random", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"].startswith("/?")
    assert "message" in response.headers["location"]

    with sqlite3.connect(get_db_path()) as conn:
        row = conn.execute(
            "SELECT title FROM episodes WHERE wallabag_id=999"
        ).fetchone()
    assert row is not None
    assert row[0] == "Mocked Article"


def test_add_random_wallabag_error(client, monkeypatch):
    from app.wallabag import WallabagError

    async def mock_add_random(n, wallabag_client, settings):
        raise WallabagError("connection refused")

    monkeypatch.setattr("app.main.add_random", mock_add_random)

    response = client.post("/queue/add-random", follow_redirects=False)

    assert response.status_code == 303
    assert "error" in response.headers["location"]


def test_delete_staged(client):
    spy = _ArchiveSpyWallabag()
    app.state.wallabag_client = spy
    with sqlite3.connect(get_db_path()) as conn:
        _insert_staged(conn, [(1, "To Delete")])
        episode_id = conn.execute(
            "SELECT id FROM episodes WHERE wallabag_id=1"
        ).fetchone()[0]

    response = client.post(f"/queue/{episode_id}/delete", follow_redirects=False)

    assert response.status_code == 303
    assert "message" in response.headers["location"]
    assert spy.archive_calls == [1]
    with sqlite3.connect(get_db_path()) as conn:
        row = conn.execute(
            "SELECT status FROM episodes WHERE id=?", (episode_id,)
        ).fetchone()
    assert row is None


def test_delete_done_succeeds(client, env):
    spy = _ArchiveSpyWallabag()
    app.state.wallabag_client = spy
    audio_path = _write_audio_file(env, 5)
    with sqlite3.connect(get_db_path()) as conn:
        _insert_done_audio(conn, 5, "Done Article", audio_path)
        episode_id = conn.execute(
            "SELECT id FROM episodes WHERE wallabag_id=5"
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO processed_articles (wallabag_id, episode_id, processed_at) "
            "VALUES (?, ?, '2026-01-02T00:00:00+00:00')",
            (5, episode_id),
        )
        conn.commit()

    response = client.post(f"/queue/{episode_id}/delete", follow_redirects=False)

    assert response.status_code == 303
    assert spy.archive_calls == [5]
    with sqlite3.connect(get_db_path()) as conn:
        row = conn.execute(
            "SELECT status FROM episodes WHERE id=?", (episode_id,)
        ).fetchone()
    assert row is None
    assert audio_path.exists() is False
    with sqlite3.connect(get_db_path()) as conn:
        processed = conn.execute(
            "SELECT 1 FROM processed_articles WHERE wallabag_id=5"
        ).fetchone()
    assert processed is None


def test_delete_wallabag_error_keeps_episode(client, env):
    from app.wallabag import WallabagError

    spy = _ArchiveSpyWallabag(error=WallabagError("connection refused"))
    app.state.wallabag_client = spy
    audio_path = _write_audio_file(env, 9)
    with sqlite3.connect(get_db_path()) as conn:
        _insert_done_audio(conn, 9, "Stuck Article", audio_path)
        episode_id = conn.execute(
            "SELECT id FROM episodes WHERE wallabag_id=9"
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO processed_articles (wallabag_id, episode_id, processed_at) "
            "VALUES (?, ?, '2026-01-02T00:00:00+00:00')",
            (9, episode_id),
        )
        conn.commit()

    response = client.post(f"/queue/{episode_id}/delete", follow_redirects=False)

    # Archive was attempted and failed: error flash + nothing deleted.
    assert response.status_code == 303
    assert "error" in response.headers["location"]
    assert spy.archive_calls == [9]
    with sqlite3.connect(get_db_path()) as conn:
        row = conn.execute(
            "SELECT status FROM episodes WHERE id=?", (episode_id,)
        ).fetchone()
        processed = conn.execute(
            "SELECT 1 FROM processed_articles WHERE wallabag_id=9"
        ).fetchone()
    assert row is not None
    assert row[0] == "done"
    assert processed is not None
    assert audio_path.exists() is True


def test_delete_nonexistent(client):
    spy = _ArchiveSpyWallabag()
    app.state.wallabag_client = spy
    response = client.post("/queue/9999/delete", follow_redirects=False)

    assert response.status_code == 303
    assert "error" in response.headers["location"]
    assert spy.archive_calls == []


def test_generate_no_staged(client):
    response = client.post("/queue/generate", follow_redirects=False)

    assert response.status_code == 303
    assert "error" in response.headers["location"]


def test_generate_starts(client, monkeypatch):
    with sqlite3.connect(get_db_path()) as conn:
        _insert_staged(conn, [(1, "Ready to Generate")])

    async def mock_generate_all(wallabag_client, kokoro_client, settings):
        return {"total": 1, "done": 1, "failed": 0, "skipped": 0}

    monkeypatch.setattr("app.main.generate_all", mock_generate_all)

    response = client.post("/queue/generate", follow_redirects=False)

    assert response.status_code == 303
    assert "generating" in response.headers["location"]


def test_generate_retries_failed_only_queue(client):
    with sqlite3.connect(get_db_path()) as conn:
        _insert_failed(conn, 7, "Broken Article")

    response = client.post("/queue/generate", follow_redirects=False)

    # The reset happens synchronously in the route: the failed-only queue no
    # longer bounces with "No staged articles to generate".
    assert response.status_code == 303
    assert "error" not in response.headers["location"]
    with sqlite3.connect(get_db_path()) as conn:
        row = conn.execute(
            "SELECT status FROM episodes WHERE wallabag_id=7"
        ).fetchone()
    assert row[0] == "staged"


def test_generate_sweeps_failed_into_run(client, monkeypatch):
    with sqlite3.connect(get_db_path()) as conn:
        _insert_staged(conn, [(1, "Fresh Article")])
        _insert_failed(conn, 2, "Broken Article")

    async def mock_generate_all(wallabag_client, kokoro_client, settings):
        return {"total": 2, "done": 2, "failed": 0, "skipped": 0}

    monkeypatch.setattr("app.main.generate_all", mock_generate_all)

    response = client.post("/queue/generate", follow_redirects=False)

    assert response.status_code == 303
    assert "error" not in response.headers["location"]
    with sqlite3.connect(get_db_path()) as conn:
        statuses = dict(
            conn.execute("SELECT wallabag_id, status FROM episodes").fetchall()
        )
    assert statuses == {1: "staged", 2: "staged"}


def test_clear_queue(client):
    with sqlite3.connect(get_db_path()) as conn:
        _insert_staged(conn, [(1, "Staged One"), (2, "Staged Two")])
        _insert_failed(conn, 3, "Failed One")
        _insert_done(conn, 4, "Done One")

    response = client.post("/queue/clear", follow_redirects=False)

    assert response.status_code == 303
    with sqlite3.connect(get_db_path()) as conn:
        remaining = dict(conn.execute("SELECT wallabag_id, status FROM episodes"))
    assert 1 not in remaining
    assert 2 not in remaining
    assert 3 not in remaining
    assert remaining[4] == "done"


# ---------------------------------------------------------------------------
# Stop generation + removable generating episodes
# ---------------------------------------------------------------------------


def test_stop_no_active_run(client):
    app.state.generating = False
    app.state.generation_task = None
    try:
        response = client.post("/queue/stop", follow_redirects=False)

        assert response.status_code == 303
        assert "error" in response.headers["location"]
    finally:
        app.state.generating = False
        app.state.generation_task = None


def test_stop_active_run(client, monkeypatch):
    with sqlite3.connect(get_db_path()) as conn:
        _insert_staged(conn, [(1, "Parked Article")])

    started = asyncio.Event()
    release = asyncio.Event()

    async def mock_generate_all(wallabag_client, kokoro_client, settings):
        started.set()
        try:
            await release.wait()
        except asyncio.CancelledError:
            # Mirror real generate_all: swallow, return partial summary.
            return {"total": 1, "done": 0, "failed": 1, "skipped": 0}
        return {"total": 1, "done": 1, "failed": 0, "skipped": 0}

    monkeypatch.setattr("app.main.generate_all", mock_generate_all)

    app.state.generating = False
    app.state.generation_task = None
    try:
        resp_generate = client.post("/queue/generate", follow_redirects=False)
        assert resp_generate.status_code == 303

        # Each sync TestClient call pumps the portal's event loop, letting the
        # scheduled generation task progress to its parked await.
        for _ in range(20):
            if started.is_set():
                break
            client.get("/health")
        assert started.is_set()

        resp_stop = client.post("/queue/stop", follow_redirects=False)
        assert resp_stop.status_code == 303
        assert "message" in resp_stop.headers["location"]

        # The pending cancel wins over release; the mock swallows it and
        # returns the partial summary, then _run_generation's finally clears
        # the handle (generation_task -> None).
        release.set()
        for _ in range(20):
            task = getattr(app.state, "generation_task", None)
            if task is None or task.done():
                break
            client.get("/health")
        task = getattr(app.state, "generation_task", None)
        assert task is None or task.done()
    finally:
        release.set()
        task = getattr(app.state, "generation_task", None)
        if task is not None and not task.done():
            task.cancel()
        app.state.generating = False
        app.state.generation_task = None


async def test_delete_active_generating_triggers_stop(client):
    spy = _ArchiveSpyWallabag()
    app.state.wallabag_client = spy
    with sqlite3.connect(get_db_path()) as conn:
        _insert_generating(conn, 42, "In Flight")
        episode_id = conn.execute(
            "SELECT id FROM episodes WHERE wallabag_id=42"
        ).fetchone()[0]

    parked = asyncio.Event()

    async def _noop_task():
        await parked.wait()

    task = asyncio.create_task(_noop_task())
    app.state.generating = True
    app.state.generation_task = task
    try:
        response = client.post(f"/queue/{episode_id}/delete", follow_redirects=False)

        assert response.status_code == 303
        assert "message" in response.headers["location"]

        # The route called task.cancel(); let the test loop deliver it. A
        # TimeoutError (not suppressed) means the route failed to cancel.
        try:
            await asyncio.wait_for(task, timeout=5)
        except asyncio.CancelledError:
            pass
        assert task.cancelled()

        # The stop branch never archives: nothing was deleted.
        assert spy.archive_calls == []

        # The row is NOT deleted — during an active run the loop owns it.
        with sqlite3.connect(get_db_path()) as conn:
            row = conn.execute(
                "SELECT status FROM episodes WHERE id=?", (episode_id,)
            ).fetchone()
        assert row is not None
        assert row[0] == "generating"
    finally:
        app.state.generating = False
        app.state.generation_task = None
        if not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task


def test_delete_orphan_generating_deletes(client):
    spy = _ArchiveSpyWallabag()
    app.state.wallabag_client = spy
    with sqlite3.connect(get_db_path()) as conn:
        _insert_generating(conn, 7, "Orphaned Episode")
        episode_id = conn.execute(
            "SELECT id FROM episodes WHERE wallabag_id=7"
        ).fetchone()[0]

    app.state.generating = False
    app.state.generation_task = None
    try:
        response = client.post(f"/queue/{episode_id}/delete", follow_redirects=False)

        assert response.status_code == 303
        assert "message" in response.headers["location"]
        assert spy.archive_calls == [7]

        with sqlite3.connect(get_db_path()) as conn:
            row = conn.execute(
                "SELECT status FROM episodes WHERE id=?", (episode_id,)
            ).fetchone()
        assert row is None
    finally:
        app.state.generating = False
        app.state.generation_task = None


# ---------------------------------------------------------------------------
# UI visibility: Stop button + generating episode remove button
# ---------------------------------------------------------------------------


def test_stop_button_shown_while_generating(client):
    with sqlite3.connect(get_db_path()) as conn:
        _insert_staged(conn, [(1, "Queued Article")])

    app.state.generating = True
    try:
        response = client.get("/")

        assert response.status_code == 200
        assert "Stop Generating" in response.text
        assert 'action="/queue/stop"' in response.text
    finally:
        app.state.generating = False
        app.state.generation_task = None


def test_stop_button_hidden_when_not_generating(client):
    app.state.generating = False
    app.state.generation_task = None
    try:
        response = client.get("/")

        assert response.status_code == 200
        assert "Stop Generating" not in response.text
    finally:
        app.state.generating = False
        app.state.generation_task = None


def test_delete_button_shown_for_generating_during_run(client):
    with sqlite3.connect(get_db_path()) as conn:
        _insert_generating(conn, 42, "In Flight")
        episode_id = conn.execute(
            "SELECT id FROM episodes WHERE wallabag_id=42"
        ).fetchone()[0]

    app.state.generating = True
    try:
        response = client.get("/")

        assert response.status_code == 200
        assert "status-badge-generating" in response.text
        # The button always renders; clicking it mid-run triggers the stop
        # flow (queue_delete cancels the task instead of deleting directly).
        assert f'action="/queue/{episode_id}/delete"' in response.text
        assert "data-confirm-message" in response.text
    finally:
        app.state.generating = False
        app.state.generation_task = None


def test_delete_button_shown_for_orphan_generating(client):
    with sqlite3.connect(get_db_path()) as conn:
        _insert_generating(conn, 7, "Orphaned Episode")
        episode_id = conn.execute(
            "SELECT id FROM episodes WHERE wallabag_id=7"
        ).fetchone()[0]

    app.state.generating = False
    app.state.generation_task = None
    try:
        response = client.get("/")

        assert response.status_code == 200
        assert f'action="/queue/{episode_id}/delete"' in response.text
        assert "data-confirm-message" in response.text
    finally:
        app.state.generating = False
        app.state.generation_task = None


def test_delete_button_shown_for_staged_and_failed(client):
    with sqlite3.connect(get_db_path()) as conn:
        _insert_staged(conn, [(1, "Staged Article")])
        _insert_failed(conn, 2, "Failed Article")
        staged_id = conn.execute(
            "SELECT id FROM episodes WHERE wallabag_id=1"
        ).fetchone()[0]
        failed_id = conn.execute(
            "SELECT id FROM episodes WHERE wallabag_id=2"
        ).fetchone()[0]

    app.state.generating = False
    app.state.generation_task = None
    try:
        response = client.get("/")

        assert response.status_code == 200
        assert f'action="/queue/{staged_id}/delete"' in response.text
        assert f'action="/queue/{failed_id}/delete"' in response.text
        # The modal prompt now covers all statuses, not just done.
        # Staged/failed copy omits the mp3-removal detail (no audio to lose).
        assert "remove its audio file" not in response.text
        assert 'data-confirm="true"' not in response.text
        assert response.text.count("data-confirm-message") >= 2
    finally:
        app.state.generating = False
        app.state.generation_task = None


def test_delete_button_shown_for_done_with_confirm(client, env):
    audio_path = _write_audio_file(env, 3)
    with sqlite3.connect(get_db_path()) as conn:
        _insert_done_audio(conn, 3, "Finished Episode", audio_path)
        episode_id = conn.execute(
            "SELECT id FROM episodes WHERE wallabag_id=3"
        ).fetchone()[0]

    app.state.generating = False
    app.state.generation_task = None
    try:
        response = client.get("/")

        assert response.status_code == 200
        assert f'action="/queue/{episode_id}/delete"' in response.text
        # The done-episode modal copy warns about irreversible mp3 loss.
        assert "data-confirm-message" in response.text
        assert "remove its audio file" in response.text
    finally:
        app.state.generating = False
        app.state.generation_task = None


def test_archive_button_absent(client):
    with sqlite3.connect(get_db_path()) as conn:
        _insert_staged(conn, [(1, "Staged Article")])
        _insert_done(conn, 2, "Done Article")

    response = client.get("/")

    assert response.status_code == 200
    assert "Archive Completed" not in response.text


# ---------------------------------------------------------------------------
# Status polling endpoint
# ---------------------------------------------------------------------------


def test_queue_status_empty(client):
    response = client.get("/queue/status")

    assert response.status_code == 200
    data = response.json()
    assert data["generating"] is False
    assert data["stats"]["articles"] == 0
    assert data["episodes"] == []


def test_queue_status_with_episodes(client):
    with sqlite3.connect(get_db_path()) as conn:
        _insert_staged(conn, [(1, "Article A"), (2, "Article B")])
        _insert_done(conn, 3, "Article C")

    response = client.get("/queue/status")

    assert response.status_code == 200
    data = response.json()
    assert data["generating"] is False
    assert data["stats"]["articles"] == 3
    assert data["stats"]["staged"] == 2
    assert data["stats"]["done"] == 1
    ids = [ep["id"] for ep in data["episodes"]]
    assert len(ids) == 3


def test_queue_status_includes_chunk_progress(client):
    with sqlite3.connect(get_db_path()) as conn:
        _insert_generating(conn, 1, "Article A")
        conn.execute(
            "UPDATE episodes SET progress_done=4, progress_total=12 WHERE id=1"
        )
        conn.commit()

    response = client.get("/queue/status")

    assert response.status_code == 200
    episodes = {ep["id"]: ep for ep in response.json()["episodes"]}
    assert episodes[1]["progress_done"] == 4
    assert episodes[1]["progress_total"] == 12


def test_home_renders_generating_row_with_progress(client):
    app.state.generating = True
    try:
        with sqlite3.connect(get_db_path()) as conn:
            _insert_generating(conn, 7, "Article G")
            conn.execute(
                "UPDATE episodes SET progress_done=4, progress_total=12 WHERE id=1"
            )
            conn.commit()

        response = client.get("/")

        assert response.status_code == 200
        assert 'id="ep-progress-1"' in response.text
        assert "4 of 12 chunks synthesized" in response.text
        assert 'id="progress-chunk"' in response.text
    finally:
        app.state.generating = False


# ---------------------------------------------------------------------------
# Settings save + Wallabag test
# ---------------------------------------------------------------------------


def test_save_settings(client):
    response = client.post(
        "/settings",
        data={"articles_per_drive": "15", "voice": "af_blossom"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert "message" in response.headers["location"]
    with sqlite3.connect(get_db_path()) as conn:
        assert get_setting(conn, "articles_per_drive") == "15"
        assert get_setting(conn, "voice") == "af_blossom"


def test_save_settings_invalid_number(client):
    response = client.post(
        "/settings",
        data={"articles_per_drive": "abc", "voice": "af_heart"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert "error" in response.headers["location"]
    with sqlite3.connect(get_db_path()) as conn:
        assert get_setting(conn, "articles_per_drive") == "10"


def test_save_settings_out_of_range(client):
    response = client.post(
        "/settings",
        data={"articles_per_drive": "100", "voice": "af_heart"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert "error" in response.headers["location"]


def test_save_settings_empty_voice(client):
    response = client.post(
        "/settings",
        data={"articles_per_drive": "10", "voice": ""},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert "error" in response.headers["location"]


def test_save_settings_json_ok(client):
    response = client.post(
        "/settings",
        data={"articles_per_drive": "12", "voice": "af_blossom"},
        headers={"Accept": "application/json"},
    )

    assert response.status_code == 200
    assert response.json() == {"ok": True}
    with sqlite3.connect(get_db_path()) as conn:
        assert get_setting(conn, "articles_per_drive") == "12"
        assert get_setting(conn, "voice") == "af_blossom"


def test_save_settings_json_invalid(client):
    response = client.post(
        "/settings",
        data={"articles_per_drive": "abc", "voice": "af_heart"},
        headers={"Accept": "application/json"},
    )

    assert response.status_code == 400
    assert response.json() == {
        "ok": False,
        "error": "Articles per drive must be a number",
    }
    with sqlite3.connect(get_db_path()) as conn:
        assert get_setting(conn, "articles_per_drive") != "abc"


def test_wallabag_test_ok(client):
    app.state.wallabag_client = _MockWallabag(connected=True)

    response = client.post("/wallabag/test", follow_redirects=False)

    assert response.status_code == 303
    assert "message" in response.headers["location"]
    assert "OK" in response.headers["location"]


def test_wallabag_test_fail(client):
    app.state.wallabag_client = _MockWallabag(connected=False)

    response = client.post("/wallabag/test", follow_redirects=False)

    assert response.status_code == 303
    assert "error" in response.headers["location"]


# ---------------------------------------------------------------------------
# Existing routes still work
# ---------------------------------------------------------------------------


def test_feed_route_still_works(client):
    response = client.get("/feed.xml")

    assert response.status_code == 200
    assert "xml" in response.headers["content-type"]


def test_health_route(client):
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


# ---------------------------------------------------------------------------
# Audio serving (range-aware)
# ---------------------------------------------------------------------------


def test_audio_serves_full_file(client, env):
    audio_path = _write_audio_file(env, 1)
    with sqlite3.connect(get_db_path()) as conn:
        _insert_done_audio(conn, 1, "Audio Episode", audio_path)

    response = client.get("/audio/1.mp3")

    assert response.status_code == 200
    assert response.headers["content-type"] == "audio/mpeg"
    assert response.headers["accept-ranges"] == "bytes"
    assert response.content == audio_path.read_bytes()


def test_audio_serves_range(client, env):
    audio_path = _write_audio_file(env, 1)
    with sqlite3.connect(get_db_path()) as conn:
        _insert_done_audio(conn, 1, "Audio Episode", audio_path)

    response = client.get("/audio/1.mp3", headers={"Range": "bytes=0-3"})

    assert response.status_code == 206
    assert response.headers["content-type"] == "audio/mpeg"
    assert response.headers["accept-ranges"] == "bytes"
    assert response.headers["content-range"] == "bytes 0-3/100"
    assert response.headers["content-length"] == "4"
    assert response.content == b"0123"


def test_audio_serves_open_ended_range(client, env):
    audio_path = _write_audio_file(env, 1)
    with sqlite3.connect(get_db_path()) as conn:
        _insert_done_audio(conn, 1, "Audio Episode", audio_path)

    response = client.get("/audio/1.mp3", headers={"Range": "bytes=2-"})

    assert response.status_code == 206
    assert response.headers["content-range"] == "bytes 2-99/100"
    assert response.content == audio_path.read_bytes()[2:]


def test_audio_serves_suffix_range(client, env):
    audio_path = _write_audio_file(env, 1)
    with sqlite3.connect(get_db_path()) as conn:
        _insert_done_audio(conn, 1, "Audio Episode", audio_path)

    response = client.get("/audio/1.mp3", headers={"Range": "bytes=-4"})

    assert response.status_code == 206
    assert response.headers["content-range"] == "bytes 96-99/100"
    assert response.content == audio_path.read_bytes()[-4:]


def test_audio_missing_episode_404(client, env):
    response = client.get("/audio/9999.mp3")

    assert response.status_code == 404


def test_audio_missing_file_404(client, env):
    with sqlite3.connect(get_db_path()) as conn:
        _insert_done_audio(conn, 1, "Missing File", env / "audio" / "nope.mp3")

    response = client.get("/audio/1.mp3")

    assert response.status_code == 404


def test_audio_invalid_range_416(client, env):
    audio_path = _write_audio_file(env, 1)
    with sqlite3.connect(get_db_path()) as conn:
        _insert_done_audio(conn, 1, "Audio Episode", audio_path)

    response = client.get("/audio/1.mp3", headers={"Range": "bytes=999999-"})

    assert response.status_code == 416
    assert response.headers["content-range"] == "bytes */100"
