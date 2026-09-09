"""Smoke tests for the scaffold: imports, DB init, and seeded settings."""

import re
import sqlite3

import pytest

from app.config import Settings, get_settings
from app.db import delete_podcast, init_db
from app.main import app  # noqa: F401  (ensures clean import / no startup side effects)

_REQUIRED_ENV = {
    "WALLABAG_CLIENT_ID": "test_client_id",
    "WALLABAG_CLIENT_SECRET": "test_client_secret",
    "WALLABAG_USERNAME": "test_user",
    "WALLABAG_PASSWORD": "test_pass",
}


def _set_required_env(monkeypatch):
    for key, value in _REQUIRED_ENV.items():
        monkeypatch.setenv(key, value)


@pytest.fixture(autouse=True)
def _fresh_settings_cache():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_init_db_creates_tables_and_seeds_settings(tmp_path, monkeypatch):
    _set_required_env(monkeypatch)
    db_path = tmp_path / "podcast.db"

    init_db(db_path)

    with sqlite3.connect(db_path) as conn:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert {"settings", "episodes", "processed_articles", "podcasts"} <= tables

        episode_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(episodes)")
        }
        assert "podcast_id" in episode_columns

        podcast = conn.execute(
            "SELECT guid, name FROM podcasts"
        ).fetchone()
        assert podcast is not None
        assert re.fullmatch(r"[0-9a-f]{8}", podcast[0])
        assert podcast[1]
        assert podcast[1] == podcast[1].title()
        assert len(podcast[1].split()) >= 2

        seeded = dict(conn.execute("SELECT key, value FROM settings"))
        assert seeded["articles_per_drive"] == "10"
        assert seeded["automation_enabled"] == "0"
        assert seeded["automation_time"] == "07:00"
        # voice seeded from KOKORO_DEFAULT_VOICE by default
        assert seeded["voice"] == Settings().KOKORO_DEFAULT_VOICE


def test_settings_parses_exclude_tags_and_required_secrets():
    settings = Settings(
        _env_file=None,
        **_REQUIRED_ENV,
        EXCLUDE_TAGS="computer, interactive, ",
    )
    assert settings.EXCLUDE_TAGS == ["computer", "interactive"]


def test_init_db_fresh_start_wipes_legacy_data(tmp_path, monkeypatch):
    """A pre-podcasts DB is wiped: episodes, processed rows, and audio."""
    _set_required_env(monkeypatch)
    db_path = tmp_path / "podcast.db"

    # Legacy schema: episodes WITHOUT progress columns or podcast_id.
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE episodes (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                wallabag_id   INTEGER UNIQUE,
                title         TEXT,
                source        TEXT,
                url           TEXT,
                status        TEXT,
                audio_path    TEXT,
                duration_sec  INTEGER,
                est_minutes   INTEGER,
                language      TEXT,
                error         TEXT,
                drive_id      INTEGER,
                created_at    TEXT,
                generated_at  TEXT
            );
            INSERT INTO episodes (wallabag_id, title, status, est_minutes)
            VALUES (1, 'Legacy Article', 'staged', 5);
            CREATE TABLE processed_articles (
                wallabag_id   INTEGER PRIMARY KEY,
                episode_id    INTEGER,
                processed_at  TEXT
            );
            INSERT INTO processed_articles (wallabag_id, episode_id, processed_at)
            VALUES (1, 1, '2026-01-01T00:00:00+00:00');
            """
        )

    audio_dir = tmp_path / "audio"
    audio_dir.mkdir()
    (audio_dir / "1.mp3").write_bytes(b"x")
    (audio_dir / "2.mp3").write_bytes(b"x")
    (audio_dir / "3.mp3.part").write_bytes(b"x")
    (audio_dir / "notes.txt").write_text("keep me")

    init_db(db_path)

    with sqlite3.connect(db_path) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(episodes)")}
        assert {"podcast_id", "progress_done", "progress_total"} <= columns
        assert conn.execute("SELECT COUNT(*) FROM episodes").fetchone()[0] == 0
        assert (
            conn.execute("SELECT COUNT(*) FROM processed_articles").fetchone()[0] == 0
        )
        podcast = conn.execute("SELECT guid FROM podcasts").fetchone()
        assert re.fullmatch(r"[0-9a-f]{8}", podcast[0])

    assert not (audio_dir / "1.mp3").exists()
    assert not (audio_dir / "2.mp3").exists()
    assert not (audio_dir / "3.mp3.part").exists()
    assert (audio_dir / "notes.txt").read_text() == "keep me"


def test_init_db_upgrade_from_pre_multipod_schema(tmp_path, monkeypatch):
    """A current-release pre-multi-podcast DB (progress columns, no podcast_id)
    is wiped on upgrade while existing user settings survive the reseed."""
    _set_required_env(monkeypatch)
    db_path = tmp_path / "podcast.db"
    audio_dir = tmp_path / "audio"
    audio_dir.mkdir()
    done_mp3 = audio_dir / "5.mp3"
    done_mp3.write_bytes(b"fake mp3")
    (audio_dir / "6.mp3.part").write_bytes(b"partial")
    (audio_dir / "notes.txt").write_text("keep me")

    # The released pre-multi-podcast app: current episodes columns minus
    # podcast_id (progress_done/progress_total present), real rows, dedupe
    # entries, and a settings table holding user-tuned values.
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE episodes (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                wallabag_id    INTEGER UNIQUE,
                title          TEXT,
                source         TEXT,
                url            TEXT,
                status         TEXT,
                audio_path     TEXT,
                duration_sec   INTEGER,
                est_minutes    INTEGER,
                language       TEXT,
                error          TEXT,
                drive_id       INTEGER,
                progress_done  INTEGER,
                progress_total INTEGER,
                created_at     TEXT,
                generated_at   TEXT
            );
            CREATE TABLE processed_articles (
                wallabag_id   INTEGER PRIMARY KEY,
                episode_id    INTEGER,
                processed_at  TEXT
            );
            CREATE TABLE settings (
                key        TEXT PRIMARY KEY,
                value      TEXT,
                updated_at TEXT
            );
            """
        )
        conn.execute(
            "INSERT INTO episodes (wallabag_id, title, source, url, status, "
            "audio_path, duration_sec, est_minutes, language, drive_id, "
            "created_at, generated_at) VALUES (?, ?, 'example.com/5', "
            "'https://example.com/5', 'done', ?, 300, 5, 'en', 1, "
            "'2026-01-01T00:00:00+00:00', '2026-01-02T00:00:00+00:00')",
            (5, "Done Article", str(done_mp3)),
        )
        conn.execute(
            "INSERT INTO episodes (wallabag_id, title, source, url, status, "
            "est_minutes, language, created_at) VALUES (?, ?, 'example.com/6', "
            "'https://example.com/6', 'staged', 5, 'en', "
            "'2026-01-01T00:00:00+00:00')",
            (6, "Staged Article"),
        )
        conn.execute(
            "INSERT INTO processed_articles (wallabag_id, episode_id, processed_at) "
            "VALUES (5, 1, '2026-01-02T00:00:00+00:00')"
        )
        conn.executemany(
            "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, "
            "'2026-01-01T00:00:00+00:00')",
            [
                ("articles_per_drive", "7"),
                ("voice", "bm_daniel"),
                ("automation_enabled", "0"),
                ("automation_time", "07:00"),
            ],
        )

    init_db(db_path)

    with sqlite3.connect(db_path) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(episodes)")}
        assert "podcast_id" in columns
        assert conn.execute("SELECT COUNT(*) FROM episodes").fetchone()[0] == 0
        assert (
            conn.execute("SELECT COUNT(*) FROM processed_articles").fetchone()[0] == 0
        )
        podcasts = conn.execute("SELECT guid FROM podcasts").fetchall()
        assert len(podcasts) == 1
        assert re.fullmatch(r"[0-9a-f]{8}", podcasts[0][0])
        settings = dict(conn.execute("SELECT key, value FROM settings"))
        assert settings["articles_per_drive"] == "7"
        assert settings["voice"] == "bm_daniel"
        assert settings["automation_enabled"] == "0"
        assert settings["automation_time"] == "07:00"

    assert not done_mp3.exists()
    assert not (audio_dir / "6.mp3.part").exists()
    assert (audio_dir / "notes.txt").read_text() == "keep me"


def test_init_db_rerun_is_idempotent(tmp_path, monkeypatch):
    """Re-running init on a podcasts-schema DB does not wipe existing rows."""
    _set_required_env(monkeypatch)
    db_path = tmp_path / "podcast.db"

    init_db(db_path)
    with sqlite3.connect(db_path) as conn:
        podcast_id = conn.execute("SELECT id FROM podcasts").fetchone()[0]
        conn.execute(
            "INSERT INTO episodes (wallabag_id, title, status, podcast_id) "
            "VALUES (1, 'Kept', 'staged', ?)",
            (podcast_id,),
        )
        conn.commit()

    init_db(db_path)

    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM podcasts").fetchone()[0] == 1
        row = conn.execute(
            "SELECT wallabag_id, status FROM episodes WHERE wallabag_id=1"
        ).fetchone()
        assert row == (1, "staged")


def test_zero_podcast_state_persists_across_boots(tmp_path, monkeypatch):
    """Deleting the last podcast leaves zero podcasts across re-boots."""
    _set_required_env(monkeypatch)
    db_path = tmp_path / "podcast.db"

    init_db(db_path)
    with sqlite3.connect(db_path) as conn:
        podcast_id = conn.execute("SELECT id FROM podcasts").fetchone()[0]
        assert delete_podcast(conn, podcast_id) is not None
        assert conn.execute("SELECT COUNT(*) FROM podcasts").fetchone()[0] == 0

    init_db(db_path)

    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM podcasts").fetchone()[0] == 0
