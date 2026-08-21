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


# ---------------------------------------------------------------------------
# Repository functions
# ---------------------------------------------------------------------------


def connect() -> sqlite3.Connection:
    """Open a new connection to the app database (caller closes it)."""
    return sqlite3.connect(get_db_path())


def get_staged_episodes(conn: sqlite3.Connection) -> list[dict]:
    """Return all staged episodes (id, wallabag_id, title, source, url,
    est_minutes, language), oldest first."""
    rows = conn.execute(
        "SELECT id, wallabag_id, title, source, url, est_minutes, language "
        "FROM episodes WHERE status='staged' ORDER BY id"
    ).fetchall()
    return [
        {
            "id": row[0],
            "wallabag_id": row[1],
            "title": row[2],
            "source": row[3],
            "url": row[4],
            "est_minutes": row[5],
            "language": row[6],
        }
        for row in rows
    ]


def set_episode_generating(conn: sqlite3.Connection, episode_id: int) -> None:
    """Mark an episode as being generated."""
    conn.execute("UPDATE episodes SET status='generating' WHERE id=?", (episode_id,))
    conn.commit()


def set_episode_done(
    conn: sqlite3.Connection,
    episode_id: int,
    audio_path: str,
    duration_sec: int,
    drive_id: int,
) -> None:
    """Mark an episode as successfully generated with its audio metadata."""
    conn.execute(
        "UPDATE episodes SET status='done', audio_path=?, duration_sec=?, "
        "drive_id=?, generated_at=? WHERE id=?",
        (audio_path, duration_sec, drive_id, _now_iso(), episode_id),
    )
    conn.commit()


def set_episode_failed(conn: sqlite3.Connection, episode_id: int, error: str) -> None:
    """Mark an episode as failed, recording the error message."""
    conn.execute(
        "UPDATE episodes SET status='failed', error=? WHERE id=?",
        (error, episode_id),
    )
    conn.commit()


def add_processed_article(
    conn: sqlite3.Connection, wallabag_id: int, episode_id: int
) -> None:
    """Record a successfully processed article in the dedupe index."""
    conn.execute(
        "INSERT OR IGNORE INTO processed_articles (wallabag_id, episode_id, "
        "processed_at) VALUES (?, ?, ?)",
        (wallabag_id, episode_id, _now_iso()),
    )
    conn.commit()


def next_drive_id(conn: sqlite3.Connection) -> int:
    """Return ``max(drive_id) + 1``, or 1 when no episode has a drive_id yet."""
    row = conn.execute("SELECT COALESCE(MAX(drive_id), 0) + 1 FROM episodes").fetchone()
    return int(row[0])
