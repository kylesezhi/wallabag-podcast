"""Smoke tests for the scaffold: imports, DB init, and seeded settings."""

import re
import sqlite3

from app.config import Settings
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
