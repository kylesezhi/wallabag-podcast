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
