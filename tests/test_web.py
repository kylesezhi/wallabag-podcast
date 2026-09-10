"""Tests for the web UI routes in app/main.py.

The env fixture points DATA_DIR at tmp_path and initializes the DB schema,
matching the pattern used in test_pipeline.py and test_rss.py. init_db
auto-creates one podcast, so most tests insert their episodes scoped to that
podcast (``_podcast()`` returns it) and drive the UI through its hub page.
Routes that need external clients (Wallabag, Kokoro) use lightweight mock
objects whose methods are async stubs; pipeline functions that run in the
background are patched at the ``app.main`` import site.
"""

import asyncio
import contextlib
import re
import sqlite3
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.db import (
    connect,
    create_podcast,
    get_db_path,
    get_newest_podcast,
    get_podcast_by_guid,
    get_podcasts,
    get_setting,
    init_db,
)
from app.main import _human_duration, app
from app.pipeline import delete_podcast
from app.wallabag import ArticleFull, ArticleMeta

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
    monkeypatch.setenv("KOKORO_DEFAULT_VOICE", "af_heart")
    monkeypatch.setenv("WALLABAG_URL", "https://wallabag.test")
    get_settings.cache_clear()
    init_db(get_db_path())
    return tmp_path


@pytest.fixture
def client(env):
    with TestClient(app) as test_client:
        yield test_client


def _podcast() -> dict:
    """Return the newest podcast (auto-created by init_db unless added to)."""
    conn = connect()
    try:
        podcast = get_newest_podcast(conn)
    finally:
        conn.close()
    assert podcast is not None
    return podcast


def _insert_staged(
    conn: sqlite3.Connection,
    entries: list[tuple[int, str]],
    podcast_id: int | None = None,
) -> None:
    """Insert staged episodes as (wallabag_id, title) pairs."""
    for wallabag_id, title in entries:
        conn.execute(
            "INSERT INTO episodes (wallabag_id, title, source, url, status, "
            "est_minutes, language, created_at, podcast_id) VALUES "
            "(?, ?, ?, ?, 'staged', 5, 'en', '2026-01-01T00:00:00+00:00', ?)",
            (
                wallabag_id,
                title,
                f"example.com/{wallabag_id}",
                f"https://example.com/{wallabag_id}",
                podcast_id,
            ),
        )
    conn.commit()


def _insert_done(
    conn: sqlite3.Connection,
    wallabag_id: int,
    title: str,
    podcast_id: int | None = None,
) -> None:
    conn.execute(
        "INSERT INTO episodes (wallabag_id, title, source, url, status, "
        "est_minutes, language, audio_path, duration_sec, drive_id, "
        "created_at, generated_at, podcast_id) VALUES (?, ?, ?, ?, 'done', "
        "5, 'en', '/tmp/audio.mp3', 300, 1, '2026-01-01T00:00:00+00:00', "
        "'2026-01-02T00:00:00+00:00', ?)",
        (
            wallabag_id,
            title,
            f"example.com/{wallabag_id}",
            f"https://example.com/{wallabag_id}",
            podcast_id,
        ),
    )
    conn.commit()


def _insert_failed(
    conn: sqlite3.Connection,
    wallabag_id: int,
    title: str,
    podcast_id: int | None = None,
) -> None:
    conn.execute(
        "INSERT INTO episodes (wallabag_id, title, source, url, status, "
        "est_minutes, language, error, created_at, podcast_id) VALUES "
        "(?, ?, ?, ?, 'failed', 5, 'en', 'some error', "
        "'2026-01-01T00:00:00+00:00', ?)",
        (
            wallabag_id,
            title,
            f"example.com/{wallabag_id}",
            f"https://example.com/{wallabag_id}",
            podcast_id,
        ),
    )
    conn.commit()


def _insert_generating(
    conn: sqlite3.Connection,
    wallabag_id: int,
    title: str,
    podcast_id: int | None = None,
) -> None:
    conn.execute(
        "INSERT INTO episodes (wallabag_id, title, source, url, status, "
        "est_minutes, language, created_at, podcast_id) VALUES "
        "(?, ?, ?, ?, 'generating', 5, 'en', '2026-01-01T00:00:00+00:00', ?)",
        (
            wallabag_id,
            title,
            f"example.com/{wallabag_id}",
            f"https://example.com/{wallabag_id}",
            podcast_id,
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
    conn: sqlite3.Connection,
    wallabag_id: int,
    title: str,
    audio_path: Path,
    podcast_id: int | None = None,
) -> None:
    """Insert a done episode whose audio_path points at a real file."""
    conn.execute(
        "INSERT INTO episodes (wallabag_id, title, source, url, status, "
        "est_minutes, language, audio_path, duration_sec, drive_id, "
        "created_at, generated_at, podcast_id) VALUES (?, ?, ?, ?, 'done', "
        "5, 'en', ?, 300, 1, '2026-01-01T00:00:00+00:00', "
        "'2026-01-02T00:00:00+00:00', ?)",
        (
            wallabag_id,
            title,
            f"example.com/{wallabag_id}",
            f"https://example.com/{wallabag_id}",
            str(audio_path),
            podcast_id,
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


def test_home_redirects_to_newest_podcast(client):
    podcast = _podcast()

    response = client.get("/", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == f"/podcast/{podcast['guid']}"


def test_home_redirects_to_newest_when_multiple(client):
    with sqlite3.connect(get_db_path()) as conn:
        newest = create_podcast(conn)

    response = client.get("/", follow_redirects=False)

    assert response.headers["location"] == f"/podcast/{newest['guid']}"


def test_home_redirect_preserves_flash_query(client):
    podcast = _podcast()

    response = client.get("/?message=hello", follow_redirects=False)

    assert response.headers["location"] == f"/podcast/{podcast['guid']}?message=hello"


def test_home_zero_podcasts_renders_empty_hub(client):
    delete_podcast(_podcast()["id"])

    response = client.get("/")

    assert response.status_code == 200
    # Empty hub CTA replaces the single-podcast drive hero.
    assert "No podcasts yet" in response.text
    assert "New Podcast" in response.text
    assert 'action="/podcasts/create"' in response.text
    assert "Active Podcast" not in response.text
    assert "podcast-tab" not in response.text


def test_home_shows_queue(client):
    podcast = _podcast()
    with sqlite3.connect(get_db_path()) as conn:
        _insert_staged(
            conn, [(1, "First Article"), (2, "Second Article")],
            podcast_id=podcast["id"],
        )

    response = client.get("/")

    assert response.status_code == 200
    assert "First Article" in response.text
    assert "Second Article" in response.text
    assert "staged" in response.text.lower()


def test_home_article_links_to_wallabag(client):
    podcast = _podcast()
    with sqlite3.connect(get_db_path()) as conn:
        _insert_staged(
            conn, [(1, "First Article"), (2, "Second Article")],
            podcast_id=podcast["id"],
        )

    response = client.get("/")

    assert response.status_code == 200
    assert 'href="' in response.text and "/view/1" in response.text
    assert 'href="' in response.text and "/view/2" in response.text
    assert 'target="_blank"' in response.text
    assert 'rel="noopener noreferrer"' in response.text


def test_hub_shows_add_article_form(client):
    podcast = _podcast()

    response = client.get(f"/podcast/{podcast['guid']}")

    assert response.status_code == 200
    assert f'action="/podcast/{podcast["guid"]}/queue/add-article"' in response.text
    assert 'name="url"' in response.text
    assert "Add Article" in response.text


def test_home_shows_done_with_duration(client):
    podcast = _podcast()
    with sqlite3.connect(get_db_path()) as conn:
        _insert_done(conn, 10, "Finished Episode", podcast_id=podcast["id"])

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
    podcast = _podcast()
    with sqlite3.connect(get_db_path()) as conn:
        _insert_staged(
            conn, [(1, "First Article"), (2, "Second Article")],
            podcast_id=podcast["id"],
        )
        conn.execute("UPDATE episodes SET est_minutes = 90 WHERE wallabag_id IN (1, 2)")
        conn.commit()

    response = client.get("/")

    assert response.status_code == 200
    assert "3 hours" in response.text


def test_home_shows_failed_with_error(client):
    podcast = _podcast()
    with sqlite3.connect(get_db_path()) as conn:
        _insert_failed(conn, 20, "Broken Article", podcast_id=podcast["id"])

    response = client.get("/")

    assert response.status_code == 200
    assert "Broken Article" in response.text
    assert "failed" in response.text.lower()
    assert "some error" in response.text


def test_home_failed_only_shows_ready_to_generate(client):
    podcast = _podcast()
    with sqlite3.connect(get_db_path()) as conn:
        _insert_failed(conn, 20, "Broken Article", podcast_id=podcast["id"])

    response = client.get("/")

    assert response.status_code == 200
    assert "Ready to generate" in response.text


def test_podcast_hub_shows_only_its_episodes(client):
    original = _podcast()
    with sqlite3.connect(get_db_path()) as conn:
        other = create_podcast(conn)
        _insert_staged(conn, [(1, "Original Episode")], podcast_id=original["id"])
        _insert_staged(conn, [(2, "Other Episode")], podcast_id=other["id"])

    response = client.get(f"/podcast/{original['guid']}")

    assert response.status_code == 200
    assert "Original Episode" in response.text
    assert "Other Episode" not in response.text


def test_podcast_hub_unknown_guid_404(client):
    response = client.get("/podcast/ffffffff")

    assert response.status_code == 404


def test_hub_shows_tabs_hero_other_podcasts_and_subscription(client):
    podcast = _podcast()
    with sqlite3.connect(get_db_path()) as conn:
        other = create_podcast(conn)
        _insert_done(conn, 1, "Done Episode", podcast_id=podcast["id"])
        _insert_staged(conn, [(2, "Staged Episode")], podcast_id=other["id"])

    response = client.get(f"/podcast/{podcast['guid']}")

    assert response.status_code == 200
    # Hero shows the active podcast's name under the Active Podcast label.
    assert "Active Podcast" in response.text
    assert podcast["name"] in response.text
    assert f'data-podcast-guid="{podcast["guid"]}"' in response.text
    # Header tabs render one pill per podcast, each with a count badge; the
    # active podcast's pill is marked.
    assert response.text.count('<a class="podcast-tab') == 2
    assert f'href="/podcast/{podcast["guid"]}"' in response.text
    assert f'href="/podcast/{other["guid"]}"' in response.text
    assert '<a class="podcast-tab active"' in response.text
    assert "podcast-tab-count" in response.text
    # The non-active podcast appears under Other Managed Podcasts with a
    # Switch link and a scoped delete form carrying the new data-redirect hook.
    assert "Other Managed Podcasts" in response.text
    assert other["name"] in response.text
    assert f'href="/podcast/{other["guid"]}"' in response.text
    assert f'action="/podcast/{other["guid"]}/delete"' in response.text
    assert 'data-redirect="/"' in response.text
    # Subscription card shows the per-podcast GUID feed URL + copy target.
    assert f"/podcast/{podcast['guid']}/feed.xml" in response.text
    assert (
        f'data-copy-url="{get_settings().BASE_URL}/podcast/{podcast["guid"]}/feed.xml"'
        in response.text
    )


def test_create_podcast(client):
    response = client.post("/podcasts/create", follow_redirects=False)

    assert response.status_code == 303
    location = response.headers["location"]
    match = re.search(r"/podcast/([0-9a-f]{8})\?", location)
    assert match is not None
    assert "message" in location
    guid = match.group(1)
    with sqlite3.connect(get_db_path()) as conn:
        podcast = get_podcast_by_guid(conn, guid)
        podcast_count = conn.execute("SELECT COUNT(*) FROM podcasts").fetchone()[0]
    assert podcast is not None
    assert podcast["guid"] == guid
    assert podcast_count == 2


def test_delete_podcast_without_run(client, env):
    podcast = _podcast()
    audio_path = _write_audio_file(env, 5)
    with sqlite3.connect(get_db_path()) as conn:
        _insert_done_audio(
            conn, 5, "Done Episode", audio_path, podcast_id=podcast["id"]
        )
        episode_id = conn.execute(
            "SELECT id FROM episodes WHERE wallabag_id=5"
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO processed_articles (wallabag_id, episode_id, processed_at) "
            "VALUES (?, ?, '2026-01-02T00:00:00+00:00')",
            (5, episode_id),
        )
        conn.commit()

    response = client.post(f"/podcast/{podcast['guid']}/delete", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"].startswith("/?")
    assert "Deleted+podcast" in response.headers["location"]
    with sqlite3.connect(get_db_path()) as conn:
        assert get_newest_podcast(conn) is None
        assert conn.execute("SELECT COUNT(*) FROM episodes").fetchone()[0] == 0
        assert (
            conn.execute("SELECT COUNT(*) FROM processed_articles").fetchone()[0] == 0
        )
    assert audio_path.exists() is False


def test_delete_podcast_returns_json_for_ajax(client):
    podcast = _podcast()

    response = client.post(
        f"/podcast/{podcast['guid']}/delete",
        headers={"Accept": "application/json"},
    )

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert "Deleted podcast" in response.json()["message"]
    with sqlite3.connect(get_db_path()) as conn:
        assert get_newest_podcast(conn) is None


async def test_delete_podcast_with_active_run_cancels_and_removes(client, env):
    podcast = _podcast()
    audio_path = _write_audio_file(env, 5)
    with sqlite3.connect(get_db_path()) as conn:
        _insert_done_audio(
            conn, 5, "Done Episode", audio_path, podcast_id=podcast["id"]
        )
        episode_id = conn.execute(
            "SELECT id FROM episodes WHERE wallabag_id=5"
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO processed_articles (wallabag_id, episode_id, processed_at) "
            "VALUES (?, ?, '2026-01-02T00:00:00+00:00')",
            (5, episode_id),
        )
        conn.commit()

    parked = asyncio.Event()

    async def _noop_task():
        await parked.wait()

    task = asyncio.create_task(_noop_task())
    app.state.generating = True
    app.state.generating_podcast_id = podcast["id"]
    app.state.generation_task = task
    try:
        response = client.post(
            f"/podcast/{podcast['guid']}/delete", follow_redirects=False
        )

        assert response.status_code == 303
        assert "message" in response.headers["location"]

        try:
            await asyncio.wait_for(task, timeout=5)
        except asyncio.CancelledError:
            pass
        assert task.cancelled()

        with sqlite3.connect(get_db_path()) as conn:
            assert get_podcast_by_guid(conn, podcast["guid"]) is None
            assert (
                conn.execute(
                    "SELECT 1 FROM episodes WHERE id=?", (episode_id,)
                ).fetchone()
                is None
            )
            assert (
                conn.execute(
                    "SELECT 1 FROM processed_articles WHERE wallabag_id=5"
                ).fetchone()
                is None
            )
        assert audio_path.exists() is False
    finally:
        app.state.generating = False
        app.state.generating_podcast_id = None
        app.state.generation_task = None
        if not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task


def test_delete_podcast_unknown_guid_404(client):
    response = client.post("/podcast/ffffffff/delete", follow_redirects=False)

    assert response.status_code == 404


def test_home_progress_counts_generating_episode(client):
    podcast = _podcast()
    app.state.generating = True
    try:
        with sqlite3.connect(get_db_path()) as conn:
            _insert_generating(conn, 100, "Mid Synthesis", podcast_id=podcast["id"])
            _insert_staged(
                conn, [(101, "Staged One"), (102, "Staged Two"), (103, "Staged Three"),
                       (104, "Staged Four"), (105, "Staged Five")],
                podcast_id=podcast["id"],
            )
            _insert_done(conn, 106, "Finished Episode", podcast_id=podcast["id"])
            _insert_failed(conn, 107, "Broken Article", podcast_id=podcast["id"])

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
    podcast = _podcast()

    async def mock_add_random(n, wallabag_client, settings, podcast_id=None):
        conn = connect()
        try:
            conn.execute(
                "INSERT INTO episodes (wallabag_id, title, source, url, status, "
                "est_minutes, language, created_at, podcast_id) VALUES "
                "(?, ?, ?, ?, 'staged', 5, 'en', '2026-01-01T00:00:00+00:00', ?)",
                (999, "Mocked Article", "example.com", "https://example.com", podcast_id),
            )
            conn.commit()
        finally:
            conn.close()
        return 1

    monkeypatch.setattr("app.main.add_random", mock_add_random)

    response = client.post(
        f"/podcast/{podcast['guid']}/queue/add-random", follow_redirects=False
    )

    assert response.status_code == 303
    assert response.headers["location"].startswith(f"/podcast/{podcast['guid']}?")
    assert "message" in response.headers["location"]

    with sqlite3.connect(get_db_path()) as conn:
        row = conn.execute(
            "SELECT title, podcast_id FROM episodes WHERE wallabag_id=999"
        ).fetchone()
    assert row is not None
    assert row[0] == "Mocked Article"
    assert row[1] == podcast["id"]


def test_add_random_no_articles_message(client, monkeypatch):
    podcast = _podcast()

    async def mock_add_random(n, wallabag_client, settings, podcast_id=None):
        return 0

    monkeypatch.setattr("app.main.add_random", mock_add_random)

    response = client.post(
        f"/podcast/{podcast['guid']}/queue/add-random", follow_redirects=False
    )

    assert response.status_code == 303
    assert response.headers["location"].startswith(f"/podcast/{podcast['guid']}?")
    assert "No+new+articles" in response.headers["location"]


def test_add_random_wallabag_error(client, monkeypatch):
    from app.wallabag import WallabagError

    podcast = _podcast()

    async def mock_add_random(n, wallabag_client, settings, podcast_id=None):
        raise WallabagError("connection refused")

    monkeypatch.setattr("app.main.add_random", mock_add_random)

    response = client.post(
        f"/podcast/{podcast['guid']}/queue/add-random", follow_redirects=False
    )

    assert response.status_code == 303
    assert response.headers["location"].startswith(f"/podcast/{podcast['guid']}?")
    assert "error" in response.headers["location"]


def test_add_random_unknown_podcast_404(client, monkeypatch):
    async def mock_add_random(n, wallabag_client, settings, podcast_id=None):
        raise AssertionError("add_random must not run for an unknown podcast")

    monkeypatch.setattr("app.main.add_random", mock_add_random)

    response = client.post("/podcast/ffffffff/queue/add-random", follow_redirects=False)

    assert response.status_code == 404


def test_add_article_success(client, monkeypatch):
    podcast = _podcast()
    seen = {}

    async def mock_add_article(ref, wallabag_client, settings, podcast_id=None):
        seen["ref"] = ref
        seen["podcast_id"] = podcast_id
        conn = connect()
        try:
            conn.execute(
                "INSERT INTO episodes (wallabag_id, title, source, url, status, "
                "est_minutes, language, created_at, podcast_id) VALUES "
                "(?, ?, ?, ?, 'staged', 5, 'en', '2026-01-01T00:00:00+00:00', ?)",
                (999, "Mocked Article", "example.com", "https://example.com", podcast_id),
            )
            conn.commit()
        finally:
            conn.close()
        return "Mocked Article"

    monkeypatch.setattr("app.main.add_article", mock_add_article)

    response = client.post(
        f"/podcast/{podcast['guid']}/queue/add-article",
        data={"url": "http://192.168.42.223:8000/view/2793"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"].startswith(f"/podcast/{podcast['guid']}?")
    assert "message" in response.headers["location"]
    assert "Mocked+Article" in response.headers["location"]
    assert seen == {"ref": "http://192.168.42.223:8000/view/2793", "podcast_id": podcast["id"]}

    with sqlite3.connect(get_db_path()) as conn:
        row = conn.execute(
            "SELECT title, podcast_id FROM episodes WHERE wallabag_id=999"
        ).fetchone()
    assert row is not None
    assert row[0] == "Mocked Article"
    assert row[1] == podcast["id"]


def test_add_article_strips_whitespace(client, monkeypatch):
    podcast = _podcast()
    seen = {}

    async def mock_add_article(ref, wallabag_client, settings, podcast_id=None):
        seen["ref"] = ref
        return "T"

    monkeypatch.setattr("app.main.add_article", mock_add_article)

    response = client.post(
        f"/podcast/{podcast['guid']}/queue/add-article",
        data={"url": "  2793  "},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert seen["ref"] == "2793"


def test_add_article_value_error(client, monkeypatch):
    podcast = _podcast()

    async def mock_add_article(ref, wallabag_client, settings, podcast_id=None):
        raise ValueError('"Deep Dive" is already in this podcast')

    monkeypatch.setattr("app.main.add_article", mock_add_article)

    response = client.post(
        f"/podcast/{podcast['guid']}/queue/add-article",
        data={"url": "2793"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"].startswith(f"/podcast/{podcast['guid']}?")
    assert "error" in response.headers["location"]
    assert "already+in+this+podcast" in response.headers["location"]


def test_add_article_wallabag_error(client, monkeypatch):
    from app.wallabag import WallabagError

    podcast = _podcast()

    async def mock_add_article(ref, wallabag_client, settings, podcast_id=None):
        raise WallabagError("Article 2793 not found in Wallabag")

    monkeypatch.setattr("app.main.add_article", mock_add_article)

    response = client.post(
        f"/podcast/{podcast['guid']}/queue/add-article",
        data={"url": "2793"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"].startswith(f"/podcast/{podcast['guid']}?")
    assert "error" in response.headers["location"]


def test_add_article_empty_field(client, monkeypatch):
    podcast = _podcast()

    async def mock_add_article(ref, wallabag_client, settings, podcast_id=None):
        raise AssertionError("add_article must not run for an empty field")

    monkeypatch.setattr("app.main.add_article", mock_add_article)

    response = client.post(
        f"/podcast/{podcast['guid']}/queue/add-article",
        data={"url": "   "},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"].startswith(f"/podcast/{podcast['guid']}?")
    assert "error" in response.headers["location"]


def test_add_article_unknown_podcast_404(client, monkeypatch):
    async def mock_add_article(ref, wallabag_client, settings, podcast_id=None):
        raise AssertionError("add_article must not run for an unknown podcast")

    monkeypatch.setattr("app.main.add_article", mock_add_article)

    response = client.post(
        "/podcast/ffffffff/queue/add-article",
        data={"url": "2793"},
        follow_redirects=False,
    )

    assert response.status_code == 404


def test_delete_staged(client):
    podcast = _podcast()
    with sqlite3.connect(get_db_path()) as conn:
        _insert_staged(conn, [(1, "To Delete")], podcast_id=podcast["id"])
        episode_id = conn.execute(
            "SELECT id FROM episodes WHERE wallabag_id=1"
        ).fetchone()[0]

    response = client.post(f"/queue/{episode_id}/delete", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"].startswith(f"/podcast/{podcast['guid']}?")
    assert "message" in response.headers["location"]
    with sqlite3.connect(get_db_path()) as conn:
        row = conn.execute(
            "SELECT status FROM episodes WHERE id=?", (episode_id,)
        ).fetchone()
    assert row is None


def test_delete_done_succeeds(client, env):
    podcast = _podcast()
    audio_path = _write_audio_file(env, 5)
    with sqlite3.connect(get_db_path()) as conn:
        _insert_done_audio(
            conn, 5, "Done Article", audio_path, podcast_id=podcast["id"]
        )
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
    assert response.headers["location"].startswith(f"/podcast/{podcast['guid']}?")
    assert "message" in response.headers["location"]
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


def test_delete_nonexistent(client):
    response = client.post("/queue/9999/delete", follow_redirects=False)

    assert response.status_code == 303
    assert "error" in response.headers["location"]


def test_confirm_delete_renders(client):
    podcast = _podcast()
    with sqlite3.connect(get_db_path()) as conn:
        _insert_staged(conn, [(1, "To Delete")], podcast_id=podcast["id"])
        episode_id = conn.execute(
            "SELECT id FROM episodes WHERE wallabag_id=1"
        ).fetchone()[0]

    response = client.get(f"/episode/{episode_id}/delete")

    assert response.status_code == 200
    assert "To Delete" in response.text
    assert f"/queue/{episode_id}/delete" in response.text


def test_confirm_delete_nonexistent(client):
    response = client.get("/episode/9999/delete", follow_redirects=False)

    assert response.status_code == 303
    assert "error" in response.headers["location"]


def test_confirm_delete_archived(client):
    with sqlite3.connect(get_db_path()) as conn:
        conn.execute(
            "INSERT INTO episodes (wallabag_id, title, source, url, status, "
            "est_minutes, language, created_at) VALUES (1, 'Archived', "
            "'example.com/1', 'https://example.com/1', 'archived', 5, 'en', "
            "'2026-01-01T00:00:00+00:00')"
        )
        conn.commit()
        episode_id = conn.execute(
            "SELECT id FROM episodes WHERE wallabag_id=1"
        ).fetchone()[0]

    response = client.get(f"/episode/{episode_id}/delete", follow_redirects=False)

    assert response.status_code == 303
    assert "error" in response.headers["location"]


def test_rss_description_includes_delete_link(env):
    from app.rss import build_feed
    import xml.etree.ElementTree as ET

    with sqlite3.connect(get_db_path()) as conn:
        _insert_done(conn, 3, "Test Article")

    feed = build_feed()
    root = ET.fromstring(feed)
    items = root.findall("channel/item")
    assert len(items) == 1
    desc = items[0].find("description").text
    content = items[0].find("{http://purl.org/rss/1.0/modules/content/}encoded")
    assert content is not None
    assert '<a href="' in content.text
    assert "/episode/" in content.text
    assert "/delete" in content.text


def test_generate_no_staged(client):
    podcast = _podcast()

    response = client.post(
        f"/podcast/{podcast['guid']}/queue/generate", follow_redirects=False
    )

    assert response.status_code == 303
    assert "error" in response.headers["location"]


def test_generate_requires_staged_in_target_podcast(client, monkeypatch):
    original = _podcast()
    with sqlite3.connect(get_db_path()) as conn:
        other = create_podcast(conn)
        _insert_staged(conn, [(1, "Other Article")], podcast_id=other["id"])

    response = client.post(
        f"/podcast/{original['guid']}/queue/generate", follow_redirects=False
    )
    assert response.status_code == 303
    assert "error" in response.headers["location"]

    async def mock_generate_all(wallabag_client, kokoro_client, settings, podcast_id=None):
        return {"total": 1, "done": 1, "failed": 0, "skipped": 0}

    monkeypatch.setattr("app.main.generate_all", mock_generate_all)

    response = client.post(
        f"/podcast/{other['guid']}/queue/generate", follow_redirects=False
    )
    assert response.status_code == 303
    assert "error" not in response.headers["location"]


# ---------------------------------------------------------------------------
# Queue actions: archive route (Wallabag mark-read, no local deletion)
# ---------------------------------------------------------------------------


def test_archive_staged(client):
    podcast = _podcast()
    spy = _ArchiveSpyWallabag()
    app.state.wallabag_client = spy
    with sqlite3.connect(get_db_path()) as conn:
        _insert_staged(conn, [(1, "To Archive")], podcast_id=podcast["id"])
        episode_id = conn.execute(
            "SELECT id FROM episodes WHERE wallabag_id=1"
        ).fetchone()[0]

    response = client.post(f"/queue/{episode_id}/archive", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"].startswith(f"/podcast/{podcast['guid']}?")
    assert "message" in response.headers["location"]
    assert spy.archive_calls == [1]
    with sqlite3.connect(get_db_path()) as conn:
        row = conn.execute(
            "SELECT status FROM episodes WHERE id=?", (episode_id,)
        ).fetchone()
    assert row is not None
    assert row[0] == "staged"


def test_archive_done_keeps_episode_and_mp3(client, env):
    podcast = _podcast()
    spy = _ArchiveSpyWallabag()
    app.state.wallabag_client = spy
    audio_path = _write_audio_file(env, 5)
    with sqlite3.connect(get_db_path()) as conn:
        _insert_done_audio(
            conn, 5, "Done Article", audio_path, podcast_id=podcast["id"]
        )
        episode_id = conn.execute(
            "SELECT id FROM episodes WHERE wallabag_id=5"
        ).fetchone()[0]

    response = client.post(f"/queue/{episode_id}/archive", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"].startswith(f"/podcast/{podcast['guid']}?")
    assert "message" in response.headers["location"]
    assert spy.archive_calls == [5]
    with sqlite3.connect(get_db_path()) as conn:
        row = conn.execute(
            "SELECT status FROM episodes WHERE id=?", (episode_id,)
        ).fetchone()
    assert row is not None
    assert row[0] == "done"
    assert audio_path.exists() is True


def test_archive_wallabag_error_keeps_episode(client, env):
    from app.wallabag import WallabagError

    podcast = _podcast()
    spy = _ArchiveSpyWallabag(error=WallabagError("connection refused"))
    app.state.wallabag_client = spy
    audio_path = _write_audio_file(env, 9)
    with sqlite3.connect(get_db_path()) as conn:
        _insert_done_audio(
            conn, 9, "Stuck Article", audio_path, podcast_id=podcast["id"]
        )
        episode_id = conn.execute(
            "SELECT id FROM episodes WHERE wallabag_id=9"
        ).fetchone()[0]

    response = client.post(f"/queue/{episode_id}/archive", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"].startswith(f"/podcast/{podcast['guid']}?")
    assert "error" in response.headers["location"]
    assert spy.archive_calls == [9]
    with sqlite3.connect(get_db_path()) as conn:
        row = conn.execute(
            "SELECT status FROM episodes WHERE id=?", (episode_id,)
        ).fetchone()
    assert row is not None
    assert row[0] == "done"
    assert audio_path.exists() is True


def test_archive_nonexistent(client):
    spy = _ArchiveSpyWallabag()
    app.state.wallabag_client = spy
    response = client.post("/queue/9999/archive", follow_redirects=False)

    assert response.status_code == 303
    assert "error" in response.headers["location"]
    assert spy.archive_calls == []


def test_generate_starts(client, monkeypatch):
    podcast = _podcast()
    with sqlite3.connect(get_db_path()) as conn:
        _insert_staged(conn, [(1, "Ready to Generate")], podcast_id=podcast["id"])

    async def mock_generate_all(wallabag_client, kokoro_client, settings, podcast_id=None):
        return {"total": 1, "done": 1, "failed": 0, "skipped": 0}

    monkeypatch.setattr("app.main.generate_all", mock_generate_all)

    response = client.post(
        f"/podcast/{podcast['guid']}/queue/generate", follow_redirects=False
    )

    assert response.status_code == 303
    assert response.headers["location"].startswith(f"/podcast/{podcast['guid']}?")
    assert "generating" in response.headers["location"]


def test_generate_already_running(client, monkeypatch):
    podcast = _podcast()
    with sqlite3.connect(get_db_path()) as conn:
        _insert_staged(conn, [(1, "Ready to Generate")], podcast_id=podcast["id"])

    async def mock_generate_all(wallabag_client, kokoro_client, settings, podcast_id=None):
        return {"total": 1, "done": 1, "failed": 0, "skipped": 0}

    monkeypatch.setattr("app.main.generate_all", mock_generate_all)

    app.state.generating = True
    try:
        response = client.post(
            f"/podcast/{podcast['guid']}/queue/generate", follow_redirects=False
        )

        assert response.status_code == 303
        assert "error" in response.headers["location"]
        assert "in+progress" in response.headers["location"]
    finally:
        app.state.generating = False
        app.state.generating_podcast_id = None
        app.state.generation_task = None


def test_generate_unknown_podcast_404(client):
    response = client.post("/podcast/ffffffff/queue/generate", follow_redirects=False)

    assert response.status_code == 404


def test_generate_retries_failed_only_queue(client, monkeypatch):
    podcast = _podcast()
    with sqlite3.connect(get_db_path()) as conn:
        _insert_failed(conn, 7, "Broken Article", podcast_id=podcast["id"])

    async def mock_generate_all(wallabag_client, kokoro_client, settings, podcast_id=None):
        return {"total": 0, "done": 0, "failed": 0, "skipped": 0}

    monkeypatch.setattr("app.main.generate_all", mock_generate_all)

    response = client.post(
        f"/podcast/{podcast['guid']}/queue/generate", follow_redirects=False
    )

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
    podcast = _podcast()
    with sqlite3.connect(get_db_path()) as conn:
        _insert_staged(conn, [(1, "Fresh Article")], podcast_id=podcast["id"])
        _insert_failed(conn, 2, "Broken Article", podcast_id=podcast["id"])

    async def mock_generate_all(wallabag_client, kokoro_client, settings, podcast_id=None):
        return {"total": 2, "done": 2, "failed": 0, "skipped": 0}

    monkeypatch.setattr("app.main.generate_all", mock_generate_all)

    response = client.post(
        f"/podcast/{podcast['guid']}/queue/generate", follow_redirects=False
    )

    assert response.status_code == 303
    assert "error" not in response.headers["location"]
    with sqlite3.connect(get_db_path()) as conn:
        statuses = dict(
            conn.execute("SELECT wallabag_id, status FROM episodes").fetchall()
        )
    assert statuses == {1: "staged", 2: "staged"}


def test_clear_queue(client):
    podcast = _podcast()
    with sqlite3.connect(get_db_path()) as conn:
        _insert_staged(
            conn, [(1, "Staged One"), (2, "Staged Two")], podcast_id=podcast["id"]
        )
        _insert_failed(conn, 3, "Failed One", podcast_id=podcast["id"])
        _insert_done(conn, 4, "Done One", podcast_id=podcast["id"])

    response = client.post(
        f"/podcast/{podcast['guid']}/queue/clear", follow_redirects=False
    )

    assert response.status_code == 303
    assert response.headers["location"].startswith(f"/podcast/{podcast['guid']}?")
    assert "Cleared+3" in response.headers["location"]
    with sqlite3.connect(get_db_path()) as conn:
        remaining = dict(conn.execute("SELECT wallabag_id, status FROM episodes"))
    assert 1 not in remaining
    assert 2 not in remaining
    assert 3 not in remaining
    assert remaining[4] == "done"


def test_clear_queue_keeps_other_podcast(client):
    original = _podcast()
    with sqlite3.connect(get_db_path()) as conn:
        other = create_podcast(conn)
        _insert_staged(conn, [(1, "Original Staged")], podcast_id=original["id"])
        _insert_staged(conn, [(2, "Other Staged")], podcast_id=other["id"])

    response = client.post(
        f"/podcast/{original['guid']}/queue/clear", follow_redirects=False
    )

    assert response.status_code == 303
    with sqlite3.connect(get_db_path()) as conn:
        remaining = dict(conn.execute("SELECT wallabag_id, status FROM episodes"))
    assert 1 not in remaining
    assert remaining[2] == "staged"


def test_clear_queue_unknown_podcast_404(client):
    response = client.post("/podcast/ffffffff/queue/clear", follow_redirects=False)

    assert response.status_code == 404


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
    podcast = _podcast()
    with sqlite3.connect(get_db_path()) as conn:
        _insert_staged(conn, [(1, "Parked Article")], podcast_id=podcast["id"])

    started = asyncio.Event()
    release = asyncio.Event()

    async def mock_generate_all(wallabag_client, kokoro_client, settings, podcast_id=None):
        started.set()
        try:
            await release.wait()
        except asyncio.CancelledError:
            # Mirror real generate_all: swallow, return partial summary.
            return {"total": 1, "done": 0, "failed": 1, "skipped": 0}
        return {"total": 1, "done": 1, "failed": 0, "skipped": 0}

    monkeypatch.setattr("app.main.generate_all", mock_generate_all)

    app.state.generating = False
    app.state.generating_podcast_id = None
    app.state.generation_task = None
    try:
        resp_generate = client.post(
            f"/podcast/{podcast['guid']}/queue/generate", follow_redirects=False
        )
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
        assert resp_stop.headers["location"].startswith(
            f"/podcast/{podcast['guid']}?"
        )

        # The pending cancel wins over release; the mock swallows it and
        # returns the partial summary, then _run_generation's finally clears
        # the handle (generation_task -> None) and the run state.
        release.set()
        for _ in range(20):
            task = getattr(app.state, "generation_task", None)
            if task is None or task.done():
                break
            client.get("/health")
        task = getattr(app.state, "generation_task", None)
        assert task is None or task.done()
        assert app.state.generating is False
        assert app.state.generating_podcast_id is None
    finally:
        release.set()
        task = getattr(app.state, "generation_task", None)
        if task is not None and not task.done():
            task.cancel()
        app.state.generating = False
        app.state.generating_podcast_id = None
        app.state.generation_task = None


async def test_delete_active_generating_triggers_stop(client):
    podcast = _podcast()
    with sqlite3.connect(get_db_path()) as conn:
        _insert_generating(conn, 42, "In Flight", podcast_id=podcast["id"])
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
        assert response.headers["location"].startswith(
            f"/podcast/{podcast['guid']}?"
        )

        # The route called task.cancel(); let the test loop deliver it. A
        # TimeoutError (not suppressed) means the route failed to cancel.
        try:
            await asyncio.wait_for(task, timeout=5)
        except asyncio.CancelledError:
            pass
        assert task.cancelled()

        # The stop branch never deletes: nothing was deleted.
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
    podcast = _podcast()
    with sqlite3.connect(get_db_path()) as conn:
        _insert_generating(conn, 7, "Orphaned Episode", podcast_id=podcast["id"])
        episode_id = conn.execute(
            "SELECT id FROM episodes WHERE wallabag_id=7"
        ).fetchone()[0]

    app.state.generating = False
    app.state.generation_task = None
    try:
        response = client.post(f"/queue/{episode_id}/delete", follow_redirects=False)

        assert response.status_code == 303
        assert "message" in response.headers["location"]
        assert response.headers["location"].startswith(
            f"/podcast/{podcast['guid']}?"
        )

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
    podcast = _podcast()
    with sqlite3.connect(get_db_path()) as conn:
        _insert_staged(conn, [(1, "Queued Article")], podcast_id=podcast["id"])

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
    podcast = _podcast()
    with sqlite3.connect(get_db_path()) as conn:
        _insert_generating(conn, 42, "In Flight", podcast_id=podcast["id"])
        episode_id = conn.execute(
            "SELECT id FROM episodes WHERE wallabag_id=42"
        ).fetchone()[0]

    app.state.generating = True
    try:
        response = client.get("/")

        assert response.status_code == 200
        assert "status-badge-generating" in response.text
        # Both buttons always render; clicking delete mid-run triggers the stop
        # flow (queue_delete cancels the task instead of deleting directly).
        assert f'action="/queue/{episode_id}/delete"' in response.text
        assert f'action="/queue/{episode_id}/archive"' in response.text
        assert "data-confirm-message" in response.text
    finally:
        app.state.generating = False
        app.state.generation_task = None


def test_delete_button_shown_for_orphan_generating(client):
    podcast = _podcast()
    with sqlite3.connect(get_db_path()) as conn:
        _insert_generating(conn, 7, "Orphaned Episode", podcast_id=podcast["id"])
        episode_id = conn.execute(
            "SELECT id FROM episodes WHERE wallabag_id=7"
        ).fetchone()[0]

    app.state.generating = False
    app.state.generation_task = None
    try:
        response = client.get("/")

        assert response.status_code == 200
        assert f'action="/queue/{episode_id}/delete"' in response.text
        assert f'action="/queue/{episode_id}/archive"' in response.text
        assert "data-confirm-message" in response.text
    finally:
        app.state.generating = False
        app.state.generation_task = None


def test_delete_button_shown_for_staged_and_failed(client):
    podcast = _podcast()
    with sqlite3.connect(get_db_path()) as conn:
        _insert_staged(conn, [(1, "Staged Article")], podcast_id=podcast["id"])
        _insert_failed(conn, 2, "Failed Article", podcast_id=podcast["id"])
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
        # Both Delete and Archive buttons render for each episode.
        assert f'action="/queue/{staged_id}/delete"' in response.text
        assert f'action="/queue/{staged_id}/archive"' in response.text
        assert f'action="/queue/{failed_id}/delete"' in response.text
        assert f'action="/queue/{failed_id}/archive"' in response.text
        # Delete confirm copy for staged/failed does NOT mention mp3.
        assert "remove its audio file" not in response.text
        assert 'data-confirm="true"' not in response.text
        # 2 episodes × 2 buttons = at least 4 data-confirm-message attrs.
        assert response.text.count("data-confirm-message") >= 4
    finally:
        app.state.generating = False
        app.state.generation_task = None


def test_delete_button_shown_for_done_with_confirm(client, env):
    podcast = _podcast()
    audio_path = _write_audio_file(env, 3)
    with sqlite3.connect(get_db_path()) as conn:
        _insert_done_audio(
            conn, 3, "Finished Episode", audio_path, podcast_id=podcast["id"]
        )
        episode_id = conn.execute(
            "SELECT id FROM episodes WHERE wallabag_id=3"
        ).fetchone()[0]

    app.state.generating = False
    app.state.generation_task = None
    try:
        response = client.get("/")

        assert response.status_code == 200
        assert f'action="/queue/{episode_id}/delete"' in response.text
        assert f'action="/queue/{episode_id}/archive"' in response.text
        # The done-episode modal copy warns about irreversible mp3 loss.
        assert "data-confirm-message" in response.text
        assert "remove its audio file" in response.text
    finally:
        app.state.generating = False
        app.state.generation_task = None


def test_clear_staged_form_has_confirmation(client):
    podcast = _podcast()
    with sqlite3.connect(get_db_path()) as conn:
        _insert_staged(conn, [(1, "Staged Article")], podcast_id=podcast["id"])
        _insert_failed(conn, 2, "Failed Article", podcast_id=podcast["id"])
        _insert_done(conn, 3, "Done Article", podcast_id=podcast["id"])

    app.state.generating = False
    app.state.generation_task = None
    try:
        response = client.get("/")

        assert response.status_code == 200
        # Clear Staged shares the confirmation modal; its copy spells out
        # that failed rows go too and done rows survive. The action is now
        # scoped to the active podcast.
        assert (
            f'action="/podcast/{podcast["guid"]}/queue/clear" data-confirm-message'
            in response.text
        )
        assert "staged and failed episodes" in response.text
        assert "Done episodes are kept" in response.text
        assert 'data-confirm-label="Clear"' in response.text
    finally:
        app.state.generating = False
        app.state.generation_task = None


def test_archive_button_absent(client):
    podcast = _podcast()
    with sqlite3.connect(get_db_path()) as conn:
        _insert_staged(conn, [(1, "Staged Article")], podcast_id=podcast["id"])
        _insert_done(conn, 2, "Done Article", podcast_id=podcast["id"])

    response = client.get("/")

    assert response.status_code == 200
    # The OLD bulk "Archive Completed" button must not reappear.
    assert "Archive Completed" not in response.text
    # Per-item Archive buttons ARE present for each episode.
    assert response.text.count('action="/queue/') >= 2  # at least delete+archive per episode


# ---------------------------------------------------------------------------
# Status polling endpoint
# ---------------------------------------------------------------------------


def test_queue_status_zero_podcasts(client):
    delete_podcast(_podcast()["id"])

    response = client.get("/queue/status")

    assert response.status_code == 200
    data = response.json()
    assert data["generating"] is False
    assert data["generating_podcast_id"] is None
    assert data["stats"]["articles"] == 0
    assert data["episodes"] == []
    assert data["podcasts"] == []


def test_queue_status_empty(client):
    response = client.get("/queue/status")

    assert response.status_code == 200
    data = response.json()
    assert data["generating"] is False
    assert data["generating_podcast_id"] is None
    assert data["stats"]["articles"] == 0
    assert data["episodes"] == []
    assert len(data["podcasts"]) == 1


def test_queue_status_with_episodes(client):
    podcast = _podcast()
    with sqlite3.connect(get_db_path()) as conn:
        _insert_staged(
            conn, [(1, "Article A"), (2, "Article B")], podcast_id=podcast["id"]
        )
        _insert_done(conn, 3, "Article C", podcast_id=podcast["id"])

    response = client.get("/queue/status")

    assert response.status_code == 200
    data = response.json()
    assert data["generating"] is False
    assert data["stats"]["articles"] == 3
    assert data["stats"]["staged"] == 2
    assert data["stats"]["done"] == 1
    ids = [ep["id"] for ep in data["episodes"]]
    assert len(ids) == 3
    assert data["podcasts"][0]["guid"] == podcast["guid"]
    assert data["podcasts"][0]["staged"] == 2
    assert data["podcasts"][0]["done"] == 1


def test_queue_status_podcast_param_scopes(client):
    original = _podcast()
    with sqlite3.connect(get_db_path()) as conn:
        other = create_podcast(conn)
        _insert_staged(conn, [(1, "Original Article")], podcast_id=original["id"])
        _insert_done(conn, 2, "Other Done", podcast_id=other["id"])

    response = client.get(f"/queue/status?podcast={other['guid']}")

    assert response.status_code == 200
    data = response.json()
    assert [ep["title"] for ep in data["episodes"]] == ["Other Done"]
    assert data["stats"]["staged"] == 0
    assert data["stats"]["done"] == 1
    by_guid = {p["guid"]: p for p in data["podcasts"]}
    assert len(by_guid) == 2
    assert by_guid[original["guid"]]["staged"] == 1
    assert by_guid[other["guid"]]["done"] == 1


def test_queue_status_unknown_podcast_param_falls_back(client):
    podcast = _podcast()
    with sqlite3.connect(get_db_path()) as conn:
        _insert_staged(conn, [(1, "Article A")], podcast_id=podcast["id"])

    response = client.get("/queue/status?podcast=ffffffff")

    assert response.status_code == 200
    data = response.json()
    assert [ep["title"] for ep in data["episodes"]] == ["Article A"]
    assert data["stats"]["staged"] == 1


def test_queue_status_includes_chunk_progress(client):
    podcast = _podcast()
    with sqlite3.connect(get_db_path()) as conn:
        episode_id = conn.execute(
            "INSERT INTO episodes (wallabag_id, title, source, url, status, "
            "est_minutes, language, created_at, podcast_id) VALUES "
            "(?, ?, ?, ?, 'generating', 5, 'en', '2026-01-01T00:00:00+00:00', ?)",
            (1, "Article A", "example.com/1", "https://example.com/1", podcast["id"]),
        ).lastrowid
        conn.execute(
            "UPDATE episodes SET progress_done=4, progress_total=12 WHERE id=?",
            (episode_id,),
        )
        conn.commit()

    response = client.get("/queue/status")

    assert response.status_code == 200
    episodes = {ep["id"]: ep for ep in response.json()["episodes"]}
    assert episodes[episode_id]["progress_done"] == 4
    assert episodes[episode_id]["progress_total"] == 12


def test_home_renders_generating_row_with_progress(client):
    podcast = _podcast()
    app.state.generating = True
    try:
        with sqlite3.connect(get_db_path()) as conn:
            episode_id = conn.execute(
                "INSERT INTO episodes (wallabag_id, title, source, url, status, "
                "est_minutes, language, created_at, podcast_id) VALUES "
                "(?, ?, ?, ?, 'generating', 5, 'en', '2026-01-01T00:00:00+00:00', ?)",
                (7, "Article G", "example.com/7", "https://example.com/7",
                 podcast["id"]),
            ).lastrowid
            conn.execute(
                "UPDATE episodes SET progress_done=4, progress_total=12 WHERE id=?",
                (episode_id,),
            )
            conn.commit()

        response = client.get("/")

        assert response.status_code == 200
        assert f'id="ep-progress-{episode_id}"' in response.text
        assert 'aria-valuenow="4"' in response.text
        assert 'aria-valuemax="12"' in response.text
        assert 'title="4 of 12 chunks synthesized"' in response.text
        assert 'id="progress-chunk"' not in response.text
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


def test_legacy_feed_route_removed(client):
    response = client.get("/feed.xml")

    assert response.status_code == 404


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


# ---------------------------------------------------------------------------
# End-to-end journey: real routes + real pipeline (clients mocked)
# ---------------------------------------------------------------------------


def _journey_articles() -> list[dict]:
    """Wallabag payloads for the journey: metadata + full HTML content."""
    articles = []
    for entry_id in (101, 102, 103):
        articles.append(
            {
                "id": entry_id,
                "title": f"Journey Article {entry_id}",
                "url": f"https://example.com/{entry_id}",
                "domain_name": "example.com",
                "reading_time": 5,
                "language": "en",
                "tags": [],
                # Long enough to clear MIN_TEXT_CHARS (default 200).
                "content": "<p>" + " ".join(["word"] * 100) + "</p>",
            }
        )
    return articles


class _JourneyWallabag:
    """Fake wallabag client serving the journey articles to the real pipeline."""

    def __init__(self, articles: list[dict]):
        self._articles = articles

    async def list_unread_metadata(self) -> list[ArticleMeta]:
        return [
            ArticleMeta(
                id=a["id"],
                title=a["title"],
                url=a["url"],
                domain_name=a["domain_name"],
                reading_time=a["reading_time"],
                language=a["language"],
                tags=a["tags"],
                is_archived=False,
                is_starred=False,
            )
            for a in self._articles
        ]

    async def get_entry(self, entry_id: int) -> ArticleFull:
        article = next(a for a in self._articles if a["id"] == entry_id)
        return ArticleFull(
            id=article["id"],
            title=article["title"],
            url=article["url"],
            domain_name=article["domain_name"],
            reading_time=article["reading_time"],
            language=article["language"],
            tags=article["tags"],
            is_archived=False,
            is_starred=False,
            content=article["content"],
        )

    async def aclose(self) -> None:
        pass


class _JourneyKokoro:
    """Fake kokoro client returning unparseable bytes (duration fallback)."""

    async def synthesize(self, text: str, voice: str | None = None) -> bytes:
        return b"FAKE_MP3_BYTES"

    async def aclose(self) -> None:
        pass


def test_podcast_journey_create_generate_delete(client, env):
    """Drive one podcast through the real routes and pipeline.

    Covers the wiring the unit tests mock away: POST /podcasts/create
    building a podcast, add-random staging into it via the real add_random,
    POST generate running the real generate_all to done episodes with audio
    on disk, per-podcast feeds, JSON podcast delete cleanup, and the home
    redirect/empty-hub transitions.
    """
    create_response = client.post("/podcasts/create", follow_redirects=False)
    assert create_response.status_code == 303
    match = re.search(r"/podcast/([0-9a-f]{8})\?", create_response.headers["location"])
    assert match is not None
    guid = match.group(1)
    with sqlite3.connect(get_db_path()) as conn:
        podcast = get_podcast_by_guid(conn, guid)
        podcasts = get_podcasts(conn)
        newest = get_newest_podcast(conn)
    assert podcast is not None
    assert newest is not None and newest["id"] == podcast["id"]
    other = next(p for p in podcasts if p["guid"] != guid)

    app.state.wallabag_client = _JourneyWallabag(_journey_articles())
    app.state.kokoro_client = _JourneyKokoro()

    response = client.post(
        f"/podcast/{guid}/queue/add-random", follow_redirects=False
    )
    assert response.status_code == 303
    assert "message" in response.headers["location"]
    with sqlite3.connect(get_db_path()) as conn:
        staged = conn.execute(
            "SELECT wallabag_id FROM episodes WHERE status='staged' AND podcast_id=?",
            (podcast["id"],),
        ).fetchall()
        other_episodes = conn.execute(
            "SELECT COUNT(*) FROM episodes WHERE podcast_id=?", (other["id"],)
        ).fetchone()[0]
    assert sorted(row[0] for row in staged) == [101, 102, 103]
    assert other_episodes == 0

    response = client.post(
        f"/podcast/{guid}/queue/generate", follow_redirects=False
    )
    assert response.status_code == 303
    assert "message" in response.headers["location"]

    deadline = time.monotonic() + 10
    while True:
        client.get("/health")
        with sqlite3.connect(get_db_path()) as conn:
            rows = conn.execute(
                "SELECT status, error FROM episodes WHERE podcast_id=?",
                (podcast["id"],),
            ).fetchall()
        failed = [row[1] for row in rows if row[0] == "failed"]
        assert not failed, f"episode failed during generation: {failed[0]}"
        done = bool(rows) and all(row[0] == "done" for row in rows)
        idle = (
            not getattr(app.state, "generating", False)
            and getattr(app.state, "generation_task", None) is None
        )
        if done and idle:
            break
        assert time.monotonic() < deadline, (
            "generation did not finish within 10s; "
            f"statuses={[tuple(row) for row in rows]}"
        )
        time.sleep(0.05)

    with sqlite3.connect(get_db_path()) as conn:
        episodes = conn.execute(
            "SELECT wallabag_id, audio_path, duration_sec FROM episodes "
            "WHERE podcast_id=? ORDER BY wallabag_id",
            (podcast["id"],),
        ).fetchall()
        processed = {
            row[0]
            for row in conn.execute("SELECT wallabag_id FROM processed_articles")
        }
    assert len(episodes) == 3
    assert all(row[0] in processed for row in episodes)
    audio_paths = [Path(row[1]) for row in episodes]
    assert all(path.exists() for path in audio_paths)
    assert all(row[2] == 300 for row in episodes)

    import xml.etree.ElementTree as ET

    feed_response = client.get(f"/podcast/{guid}/feed.xml")
    assert feed_response.status_code == 200
    root = ET.fromstring(feed_response.content)
    assert root.find("channel/title").text == podcast["name"]
    items = root.findall("channel/item")
    assert len(items) == 3
    assert {item.find("title").text for item in items} == {
        f"Journey Article {entry_id}" for entry_id in (101, 102, 103)
    }
    other_feed = ET.fromstring(
        client.get(f"/podcast/{other['guid']}/feed.xml").content
    )
    assert other_feed.findall("channel/item") == []

    response = client.post(
        f"/podcast/{guid}/delete", headers={"Accept": "application/json"}
    )
    assert response.status_code == 200
    assert response.json()["ok"] is True
    with sqlite3.connect(get_db_path()) as conn:
        assert conn.execute("SELECT COUNT(*) FROM episodes").fetchone()[0] == 0
        assert (
            conn.execute("SELECT COUNT(*) FROM processed_articles").fetchone()[0] == 0
        )
        remaining = conn.execute("SELECT guid FROM podcasts").fetchall()
        newest = get_newest_podcast(conn)
    assert [row[0] for row in remaining] == [other["guid"]]
    assert newest is not None and newest["id"] == other["id"]
    assert all(not path.exists() for path in audio_paths)

    response = client.get("/", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == f"/podcast/{other['guid']}"

    response = client.post(
        f"/podcast/{other['guid']}/delete", headers={"Accept": "application/json"}
    )
    assert response.status_code == 200
    assert response.json()["ok"] is True
    response = client.get("/")
    assert response.status_code == 200
    assert "No podcasts yet" in response.text
    assert "New Podcast" in response.text
