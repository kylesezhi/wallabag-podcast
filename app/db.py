"""SQLite repository module (thin, no ORM).

The database lives at ``DATA_DIR/podcast.db``. Connection-per-call is fine for
the single-user scope of this app; later tasks add repository functions here.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .config import get_settings

_SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key        TEXT PRIMARY KEY,
    value      TEXT,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS episodes (
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

CREATE TABLE IF NOT EXISTS processed_articles (
    wallabag_id   INTEGER PRIMARY KEY,
    episode_id    INTEGER,
    processed_at  TEXT
);
"""

# Default tunables seeded into the `settings` table on first run.
# `voice` defaults to KOKORO_DEFAULT_VOICE from Settings and can be overridden
# at runtime via the settings UI.
_DEFAULT_SETTINGS = (
    ("articles_per_drive", "10"),
    ("voice", "af_heart"),
    ("automation_enabled", "0"),
    ("automation_time", "07:00"),
)


def get_db_path() -> Path:
    """Return the path to the SQLite database under DATA_DIR."""
    return get_settings().DATA_DIR / "podcast.db"


def init_db(db_path: Path | None = None) -> None:
    """Create the schema and seed default settings rows on first run."""
    path = Path(db_path or get_db_path())
    path.parent.mkdir(parents=True, exist_ok=True)

    with sqlite3.connect(path) as conn:
        conn.executescript(_SCHEMA)

        now = _now_iso()
        settings = get_settings()
        for key, value in _DEFAULT_SETTINGS:
            # Seeded voice default follows KOKORO_DEFAULT_VOICE when available.
            if key == "voice":
                value = settings.KOKORO_DEFAULT_VOICE
            conn.execute(
                "INSERT OR IGNORE INTO settings (key, value, updated_at) VALUES (?, ?, ?)",
                (key, value, now),
            )


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
