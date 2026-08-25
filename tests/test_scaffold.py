"""Smoke tests for the scaffold: imports, DB init, and seeded settings."""

import sqlite3

from app.config import Settings
from app.db import init_db
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
        assert {"settings", "episodes", "processed_articles"} <= tables

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


def test_init_db_migrates_legacy_episodes_table(tmp_path, monkeypatch):
    """A DB created before chunk progress columns is upgraded in place."""
    _set_required_env(monkeypatch)
    db_path = tmp_path / "podcast.db"

    # Legacy schema: episodes WITHOUT progress_done / progress_total.
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
            """
        )

    init_db(db_path)

    with sqlite3.connect(db_path) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(episodes)")}
        assert {"progress_done", "progress_total"} <= columns
        row = conn.execute(
            "SELECT status, progress_done, progress_total FROM episodes WHERE id=1"
        ).fetchone()
        assert row[0] == "staged"
        assert row[1] is None and row[2] is None

    # Re-running init stays idempotent.
    init_db(db_path)
    with sqlite3.connect(db_path) as conn:
        count = conn.execute("SELECT COUNT(*) FROM episodes").fetchone()[0]
        assert count == 1
