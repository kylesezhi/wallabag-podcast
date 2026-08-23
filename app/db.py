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


def get_feed_episodes(conn: sqlite3.Connection) -> list[dict]:
    """Return done episodes for the podcast feed, newest generated first.

    Only ``status='done'`` episodes are included (archived episodes are
    excluded by status). Keys: id, wallabag_id, title, source, url,
    audio_path, duration_sec, generated_at.
    """
    rows = conn.execute(
        "SELECT id, wallabag_id, title, source, url, audio_path, duration_sec, "
        "generated_at FROM episodes WHERE status='done' "
        "ORDER BY generated_at DESC"
    ).fetchall()
    return [
        {
            "id": row[0],
            "wallabag_id": row[1],
            "title": row[2],
            "source": row[3],
            "url": row[4],
            "audio_path": row[5],
            "duration_sec": row[6],
            "generated_at": row[7],
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


def get_processed_wallabag_ids(conn: sqlite3.Connection) -> set[int]:
    """Return the wallabag_ids recorded in processed_articles (dedupe index)."""
    return {
        row[0]
        for row in conn.execute("SELECT wallabag_id FROM processed_articles")
        if row[0] is not None
    }


def get_staged_wallabag_ids(conn: sqlite3.Connection) -> set[int]:
    """Return the wallabag_ids that already have an episode row.

    Any status (staged/generating/done/failed/archived) counts, so an article
    that has ever entered the queue is not re-staged by add_random().
    """
    return {
        row[0]
        for row in conn.execute(
            "SELECT wallabag_id FROM episodes WHERE status IN "
            "('staged','generating','done','failed','archived')"
        )
        if row[0] is not None
    }


def insert_staged_episode(
    conn: sqlite3.Connection,
    wallabag_id: int,
    title: str,
    source: str,
    url: str,
    est_minutes: int,
    language: str | None,
) -> None:
    """Insert a new staged episode (``INSERT OR IGNORE`` on wallabag_id)."""
    conn.execute(
        "INSERT OR IGNORE INTO episodes (wallabag_id, title, source, url, status, "
        "est_minutes, language, created_at) VALUES (?, ?, ?, ?, 'staged', ?, ?, ?)",
        (wallabag_id, title, source, url, est_minutes, language, _now_iso()),
    )
    conn.commit()


def delete_episode(conn: sqlite3.Connection, episode_id: int) -> int:
    """Delete a staged|failed|generating episode by id. Return rowcount (0 or 1)."""
    cur = conn.execute(
        "DELETE FROM episodes WHERE id=? AND status IN "
        "('staged','failed','generating')",
        (episode_id,),
    )
    conn.commit()
    return cur.rowcount


def archive_done_episodes(conn: sqlite3.Connection) -> int:
    """Set status done->archived for all done episodes. Return rowcount."""
    cur = conn.execute("UPDATE episodes SET status='archived' WHERE status='done'")
    conn.commit()
    return cur.rowcount


def delete_staged_failed_episodes(conn: sqlite3.Connection) -> int:
    """Delete all staged|failed episodes. Return rowcount."""
    cur = conn.execute("DELETE FROM episodes WHERE status IN ('staged','failed')")
    conn.commit()
    return cur.rowcount


def get_episode_status(conn: sqlite3.Connection, episode_id: int) -> str | None:
    """Return the episode's status, or None if no such episode exists."""
    row = conn.execute("SELECT status FROM episodes WHERE id=?", (episode_id,)).fetchone()
    return row[0] if row is not None else None


def get_queue_episodes(conn: sqlite3.Connection) -> list[dict]:
    """Return the visible queue (staged/generating/done/failed), oldest first.

    Archived episodes are hidden. Keys: id, wallabag_id, title, source, url,
    status, est_minutes, duration_sec, error.
    """
    rows = conn.execute(
        "SELECT id, wallabag_id, title, source, url, status, est_minutes, "
        "duration_sec, error "
        "FROM episodes WHERE status IN ('staged','generating','done','failed') "
        "ORDER BY id"
    ).fetchall()
    return [
        {
            "id": row[0],
            "wallabag_id": row[1],
            "title": row[2],
            "source": row[3],
            "url": row[4],
            "status": row[5],
            "est_minutes": row[6],
            "duration_sec": row[7],
            "error": row[8],
        }
        for row in rows
    ]


def has_staged_episodes(conn: sqlite3.Connection) -> bool:
    """Return True when at least one episode is staged (ready to generate)."""
    row = conn.execute(
        "SELECT 1 FROM episodes WHERE status='staged' LIMIT 1"
    ).fetchone()
    return row is not None


def get_setting(conn: sqlite3.Connection, key: str) -> str | None:
    """Return a UI-tunable setting value, or None when the key is unknown."""
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row[0] if row is not None else None


def set_setting(conn: sqlite3.Connection, key: str, value: str) -> None:
    """Upsert a UI-tunable setting."""
    conn.execute(
        "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value, "
        "updated_at=excluded.updated_at",
        (key, value, _now_iso()),
    )
    conn.commit()


def get_stats_rows(conn: sqlite3.Connection) -> dict:
    """Return the aggregates needed by :func:`app.pipeline.stats`.

    Shape: ``{"status_counts": {status: count}, "staged_minutes": int,
    "done_seconds": int, "done_drive_id": int | None}``.
    """
    status_counts = {
        row[0]: row[1]
        for row in conn.execute("SELECT status, COUNT(*) FROM episodes GROUP BY status")
    }
    staged_minutes = conn.execute(
        "SELECT COALESCE(SUM(est_minutes), 0) FROM episodes WHERE status='staged'"
    ).fetchone()[0]
    done_seconds = conn.execute(
        "SELECT COALESCE(SUM(duration_sec), 0) FROM episodes WHERE status='done'"
    ).fetchone()[0]
    done_drive_id = conn.execute(
        "SELECT MAX(drive_id) FROM episodes WHERE status='done'"
    ).fetchone()[0]
    return {
        "status_counts": status_counts,
        "staged_minutes": int(staged_minutes or 0),
        "done_seconds": int(done_seconds or 0),
        "done_drive_id": done_drive_id,
    }
