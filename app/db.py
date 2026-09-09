"""SQLite repository module (thin, no ORM).

The database lives at ``DATA_DIR/podcast.db``. Connection-per-call is fine for
the single-user scope of this app; later tasks add repository functions here.
"""

from __future__ import annotations

import logging
import random
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .config import get_settings
from .naming import generate_name

logger = logging.getLogger(__name__)

_EPISODES_TABLE = """
CREATE TABLE IF NOT EXISTS episodes (
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
    generated_at   TEXT,
    podcast_id     INTEGER
);
"""

_SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key        TEXT PRIMARY KEY,
    value      TEXT,
    updated_at TEXT
);

""" + _EPISODES_TABLE + """

CREATE INDEX IF NOT EXISTS idx_episodes_podcast_id ON episodes(podcast_id);

CREATE TABLE IF NOT EXISTS podcasts (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    guid       TEXT UNIQUE NOT NULL,
    name       TEXT UNIQUE NOT NULL,
    created_at TEXT
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
    """Create the schema, migrate legacy databases, and seed default settings.

    When the ``podcasts`` table did not exist before this call (an upgrade from
    an old-schema database or a brand-new database), a fresh start is performed:
    episodes, processed_articles rows, and any generated audio are wiped, and a
    single randomly named podcast is created. Databases that already have the
    ``podcasts`` table are left untouched (idempotent on every normal boot).
    """
    path = Path(db_path or get_db_path())
    path.parent.mkdir(parents=True, exist_ok=True)

    with sqlite3.connect(path) as conn:
        had_podcasts = (
            conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='podcasts'"
            ).fetchone()
            is not None
        )

        wiped_episodes = 0
        if not had_podcasts:
            # Fresh start from a legacy DB: drop the old-shape episodes table
            # before the schema runs, so the podcast_id column and its index can
            # be created on the recreated table.
            episodes_exists = (
                conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='episodes'"
                ).fetchone()
                is not None
            )
            if episodes_exists:
                wiped_episodes = conn.execute(
                    "SELECT COUNT(*) FROM episodes"
                ).fetchone()[0]
                conn.execute("DROP TABLE episodes")

        conn.executescript(_SCHEMA)

        if not had_podcasts:
            wiped_processed = conn.execute(
                "DELETE FROM processed_articles"
            ).rowcount
            wiped_audio = _wipe_audio_files(path.parent / "audio")
            podcast = create_podcast(conn)
            logger.info(
                "fresh-start migration executed, wiped %s legacy episodes / "
                "%s processed rows / %s audio files, created podcast '%s' (%s)",
                wiped_episodes,
                wiped_processed,
                wiped_audio,
                podcast["name"],
                podcast["guid"],
            )

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


def _wipe_audio_files(audio_dir: Path) -> int:
    """Best-effort delete every ``*.mp3`` and ``*.part`` in the audio dir.

    Returns the number of files removed. ``OSError`` is swallowed so a locked
    or missing file never aborts startup.
    """
    if not audio_dir.is_dir():
        return 0
    count = 0
    for path in audio_dir.iterdir():
        if path.suffix in (".mp3", ".part"):
            try:
                path.unlink(missing_ok=True)
                count += 1
            except OSError:
                continue
    return count


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Repository functions
# ---------------------------------------------------------------------------


def connect() -> sqlite3.Connection:
    """Open a new connection to the app database (caller closes it)."""
    return sqlite3.connect(get_db_path())


def create_podcast(conn: sqlite3.Connection) -> dict:
    """Create a new randomly named podcast and return its record.

    The guid is an 8-character lowercase hex string, regenerated while it
    collides with an existing guid (bounded, mirroring :func:`generate_name`).
    The name is Title Case and unique among existing podcasts.
    """
    existing_names = {
        row[0] for row in conn.execute("SELECT name FROM podcasts")
    }
    existing_guids = {
        row[0] for row in conn.execute("SELECT guid FROM podcasts")
    }
    for _ in range(200):
        guid = uuid.uuid4().hex[:8]
        if guid not in existing_guids:
            break
    else:
        raise RuntimeError("could not generate a unique podcast guid")
    name = generate_name(existing_names)
    created_at = _now_iso()
    conn.execute(
        "INSERT INTO podcasts (guid, name, created_at) VALUES (?, ?, ?)",
        (guid, name, created_at),
    )
    conn.commit()
    podcast_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    return {
        "id": podcast_id,
        "guid": guid,
        "name": name,
        "created_at": created_at,
    }


def get_podcast_by_guid(conn: sqlite3.Connection, guid: str) -> dict | None:
    """Return a podcast by guid, or None when no such podcast exists."""
    row = conn.execute(
        "SELECT id, guid, name, created_at FROM podcasts WHERE guid=?", (guid,)
    ).fetchone()
    if row is None:
        return None
    return {"id": row[0], "guid": row[1], "name": row[2], "created_at": row[3]}


def get_newest_podcast(conn: sqlite3.Connection) -> dict | None:
    """Return the most recently created podcast (highest id), or None."""
    row = conn.execute(
        "SELECT id, guid, name, created_at FROM podcasts ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if row is None:
        return None
    return {"id": row[0], "guid": row[1], "name": row[2], "created_at": row[3]}


def get_podcasts(conn: sqlite3.Connection) -> list[dict]:
    """Return every podcast with per-podcast episode aggregates, oldest first.

    Keys: id, guid, name, created_at, staged, generating, done, failed,
    staged_minutes, done_seconds. Podcasts with zero episodes appear with zeros.
    """
    rows = conn.execute(
        "SELECT p.id, p.guid, p.name, p.created_at, "
        "COALESCE(SUM(CASE WHEN e.status='staged' THEN 1 ELSE 0 END), 0) AS staged, "
        "COALESCE(SUM(CASE WHEN e.status='generating' THEN 1 ELSE 0 END), 0) "
        "AS generating, "
        "COALESCE(SUM(CASE WHEN e.status='done' THEN 1 ELSE 0 END), 0) AS done, "
        "COALESCE(SUM(CASE WHEN e.status='failed' THEN 1 ELSE 0 END), 0) AS failed, "
        "COALESCE(SUM(CASE WHEN e.status='staged' THEN e.est_minutes ELSE 0 END), 0) "
        "AS staged_minutes, "
        "COALESCE(SUM(CASE WHEN e.status='done' THEN e.duration_sec ELSE 0 END), 0) "
        "AS done_seconds "
        "FROM podcasts p LEFT JOIN episodes e ON e.podcast_id=p.id "
        "GROUP BY p.id ORDER BY p.id ASC"
    ).fetchall()
    return [
        {
            "id": row[0],
            "guid": row[1],
            "name": row[2],
            "created_at": row[3],
            "staged": row[4],
            "generating": row[5],
            "done": row[6],
            "failed": row[7],
            "staged_minutes": row[8],
            "done_seconds": row[9],
        }
        for row in rows
    ]


def delete_podcast(conn: sqlite3.Connection, podcast_id: int) -> dict | None:
    """Delete a podcast and all of its episode and processed rows.

    Returns ``{"id", "guid", "name", "episode_count", "episode_ids",
    "audio_paths", "wallabag_ids"}``, or None when the podcast id is unknown.
    The mp3 files are deliberately NOT unlinked here; a later step
    orchestrates that with the returned episode ids and audio paths.
    """
    podcast = conn.execute(
        "SELECT id, guid, name FROM podcasts WHERE id=?", (podcast_id,)
    ).fetchone()
    if podcast is None:
        return None
    episodes = conn.execute(
        "SELECT id, wallabag_id, audio_path FROM episodes WHERE podcast_id=?",
        (podcast_id,),
    ).fetchall()
    episode_ids = [row[0] for row in episodes]
    wallabag_ids = [row[1] for row in episodes]
    audio_paths = [row[2] for row in episodes if row[2] is not None]
    conn.execute("DELETE FROM episodes WHERE podcast_id=?", (podcast_id,))
    if wallabag_ids:
        placeholders = ",".join("?" for _ in wallabag_ids)
        conn.execute(
            f"DELETE FROM processed_articles WHERE wallabag_id IN ({placeholders})",
            wallabag_ids,
        )
    conn.execute("DELETE FROM podcasts WHERE id=?", (podcast_id,))
    conn.commit()
    return {
        "id": podcast[0],
        "guid": podcast[1],
        "name": podcast[2],
        "episode_count": len(episodes),
        "episode_ids": episode_ids,
        "audio_paths": audio_paths,
        "wallabag_ids": wallabag_ids,
    }


def get_staged_episodes(
    conn: sqlite3.Connection, podcast_id: int | None = None
) -> list[dict]:
    """Return staged episodes (id, wallabag_id, title, source, url,
    est_minutes, language), oldest first.

    When ``podcast_id`` is given, only that podcast's staged episodes are
    returned; otherwise all staged episodes are returned (global behavior).
    """
    where = "WHERE status='staged'"
    params: tuple = ()
    if podcast_id is not None:
        where += " AND podcast_id=?"
        params = (podcast_id,)
    rows = conn.execute(
        f"SELECT id, wallabag_id, title, source, url, est_minutes, language "
        f"FROM episodes {where} ORDER BY id",
        params,
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


def get_feed_episodes(
    conn: sqlite3.Connection, podcast_id: int | None = None
) -> list[dict]:
    """Return done episodes for a podcast feed, newest generated first.

    Only ``status='done'`` episodes are included (archived episodes are
    excluded by status). When ``podcast_id`` is given, only that podcast's
    done episodes are returned; otherwise all done episodes are returned
    (global behavior). Keys: id, wallabag_id, title, source, url,
    audio_path, duration_sec, generated_at.
    """
    where = "WHERE status='done'"
    params: tuple = ()
    if podcast_id is not None:
        where += " AND podcast_id=?"
        params = (podcast_id,)
    rows = conn.execute(
        "SELECT id, wallabag_id, title, source, url, audio_path, duration_sec, "
        f"generated_at FROM episodes {where} ORDER BY generated_at DESC",
        params,
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


def set_episode_progress(
    conn: sqlite3.Connection, episode_id: int, done: int, total: int
) -> None:
    """Record chunk synthesis progress (done/total) for a generating episode."""
    conn.execute(
        "UPDATE episodes SET progress_done=?, progress_total=? WHERE id=?",
        (done, total, episode_id),
    )
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


def reset_failed_to_staged(
    conn: sqlite3.Connection, podcast_id: int | None = None
) -> int:
    """Re-queue failed episodes for generation. Return rowcount.

    Clears the recorded error and chunk progress so a retried episode
    starts clean. When ``podcast_id`` is given, only that podcast's failed
    episodes are reset; otherwise all failed episodes are reset.
    """
    where = "WHERE status='failed'"
    params: tuple = ()
    if podcast_id is not None:
        where += " AND podcast_id=?"
        params = (podcast_id,)
    cur = conn.execute(
        "UPDATE episodes SET status='staged', error=NULL, progress_done=NULL, "
        f"progress_total=NULL {where}",
        params,
    )
    conn.commit()
    return cur.rowcount


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
    podcast_id: int | None = None,
) -> None:
    """Insert a new staged episode (``INSERT OR IGNORE`` on wallabag_id).

    When ``podcast_id`` is given it is stored on the episode row; otherwise
    the column stays NULL.
    """
    conn.execute(
        "INSERT OR IGNORE INTO episodes (wallabag_id, title, source, url, status, "
        "est_minutes, language, created_at, podcast_id) "
        "VALUES (?, ?, ?, ?, 'staged', ?, ?, ?, ?)",
        (wallabag_id, title, source, url, est_minutes, language, _now_iso(), podcast_id),
    )
    conn.commit()


def delete_episode(
    conn: sqlite3.Connection, episode_id: int
) -> tuple[int, str | None, str] | None:
    """Delete a staged|failed|generating|done episode by id.

    Returns ``(wallabag_id, audio_path, status)`` for the deleted row, or
    None when no row matched (unknown id, or the episode is archived).
    """
    row = conn.execute(
        "SELECT wallabag_id, audio_path, status FROM episodes WHERE id=? "
        "AND status IN ('staged','failed','generating','done')",
        (episode_id,),
    ).fetchone()
    if row is None:
        return None
    conn.execute("DELETE FROM episodes WHERE id=?", (episode_id,))
    conn.commit()
    return row[0], row[1], row[2]


def delete_processed_article(conn: sqlite3.Connection, wallabag_id: int) -> None:
    """Remove a wallabag_id from the processed_articles dedupe index."""
    conn.execute("DELETE FROM processed_articles WHERE wallabag_id=?", (wallabag_id,))
    conn.commit()


def delete_staged_failed_episodes(
    conn: sqlite3.Connection, podcast_id: int | None = None
) -> int:
    """Delete staged|failed episodes. Return rowcount.

    When ``podcast_id`` is given, only that podcast's staged/failed episodes
    are deleted; otherwise all staged/failed episodes are deleted.
    """
    where = "WHERE status IN ('staged','failed')"
    params: tuple = ()
    if podcast_id is not None:
        where += " AND podcast_id=?"
        params = (podcast_id,)
    cur = conn.execute(f"DELETE FROM episodes {where}", params)
    conn.commit()
    return cur.rowcount


def get_episode_status(conn: sqlite3.Connection, episode_id: int) -> str | None:
    """Return the episode's status, or None if no such episode exists."""
    row = conn.execute("SELECT status FROM episodes WHERE id=?", (episode_id,)).fetchone()
    return row[0] if row is not None else None


def get_episode_wallabag_id(conn: sqlite3.Connection, episode_id: int) -> int | None:
    """Return the episode's wallabag_id, or None if no such episode exists."""
    row = conn.execute(
        "SELECT wallabag_id FROM episodes WHERE id=?", (episode_id,)
    ).fetchone()
    return int(row[0]) if row is not None else None


def get_episode_progress(
    conn: sqlite3.Connection, episode_id: int
) -> tuple[int | None, int | None]:
    """Return ``(progress_done, progress_total)`` persisted for the episode.

    ``(None, None)`` when no progress has been recorded — the episode failed
    before chunk synthesis began (e.g. the Wallabag fetch raised). Used by the
    generation pipeline to annotate failure logs with the chunk position.
    """
    row = conn.execute(
        "SELECT progress_done, progress_total FROM episodes WHERE id=?",
        (episode_id,),
    ).fetchone()
    if row is None:
        return None, None
    return row[0], row[1]


def get_queue_episodes(
    conn: sqlite3.Connection, podcast_id: int | None = None
) -> list[dict]:
    """Return the visible queue (staged/generating/done/failed), oldest first.

    Archived episodes are hidden. Keys: id, wallabag_id, title, source, url,
    status, est_minutes, duration_sec, error, progress_done, progress_total.
    When ``podcast_id`` is given, only that podcast's episodes are returned;
    otherwise all visible episodes are returned.
    """
    where = "WHERE status IN ('staged','generating','done','failed')"
    params: tuple = ()
    if podcast_id is not None:
        where += " AND podcast_id=?"
        params = (podcast_id,)
    rows = conn.execute(
        "SELECT id, wallabag_id, title, source, url, status, est_minutes, "
        f"duration_sec, error, progress_done, progress_total "
        f"FROM episodes {where} ORDER BY id",
        params,
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
            "progress_done": row[9],
            "progress_total": row[10],
        }
        for row in rows
    ]


def has_staged_episodes(
    conn: sqlite3.Connection, podcast_id: int | None = None
) -> bool:
    """Return True when at least one episode is staged (ready to generate).

    When ``podcast_id`` is given, only that podcast's staged episodes count;
    otherwise any staged episode counts.
    """
    where = "WHERE status='staged'"
    params: tuple = ()
    if podcast_id is not None:
        where += " AND podcast_id=?"
        params = (podcast_id,)
    row = conn.execute(f"SELECT 1 FROM episodes {where} LIMIT 1", params).fetchone()
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


def get_stats_rows(
    conn: sqlite3.Connection, podcast_id: int | None = None
) -> dict:
    """Return the aggregates needed by :func:`app.pipeline.stats`.

    Shape: ``{"status_counts": {status: count}, "staged_minutes": int,
    "done_seconds": int, "done_drive_id": int | None}``. When ``podcast_id``
    is given the aggregates cover only that podcast's episodes; otherwise the
    whole queue is summarized.
    """
    status_where = ""
    status_params: tuple = ()
    if podcast_id is not None:
        status_where = " WHERE podcast_id=?"
        status_params = (podcast_id,)
    status_counts = {
        row[0]: row[1]
        for row in conn.execute(
            f"SELECT status, COUNT(*) FROM episodes{status_where} GROUP BY status",
            status_params,
        )
    }
    staged_where = "WHERE status='staged'"
    staged_params: tuple = ()
    if podcast_id is not None:
        staged_where += " AND podcast_id=?"
        staged_params = (podcast_id,)
    staged_minutes = conn.execute(
        f"SELECT COALESCE(SUM(est_minutes), 0) FROM episodes {staged_where}",
        staged_params,
    ).fetchone()[0]
    done_where = "WHERE status='done'"
    done_params: tuple = ()
    if podcast_id is not None:
        done_where += " AND podcast_id=?"
        done_params = (podcast_id,)
    done_seconds = conn.execute(
        f"SELECT COALESCE(SUM(duration_sec), 0) FROM episodes {done_where}",
        done_params,
    ).fetchone()[0]
    done_drive_id = conn.execute(
        f"SELECT MAX(drive_id) FROM episodes {done_where}",
        done_params,
    ).fetchone()[0]
    return {
        "status_counts": status_counts,
        "staged_minutes": int(staged_minutes or 0),
        "done_seconds": int(done_seconds or 0),
        "done_drive_id": done_drive_id,
    }
