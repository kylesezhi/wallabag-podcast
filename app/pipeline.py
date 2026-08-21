"""Queue orchestration: the ``generate_all()`` episode generation flow.

Walks every ``staged`` episode and produces one MP3 per article: fetch the
full Wallabag entry, clean the HTML into TTS input, synthesize with Kokoro,
write the audio under ``DATA_DIR/audio/{id}.mp3``, measure its duration, and
mark the episode ``done`` (recording a processed_articles row). Failures are
isolated per episode: a bad article marks that episode ``failed`` and the run
continues with the next one.
"""

from __future__ import annotations

import logging
import sqlite3

from .config import Settings, get_settings
from .db import (
    add_processed_article,
    connect,
    get_staged_episodes,
    next_drive_id,
    set_episode_done,
    set_episode_failed,
    set_episode_generating,
)
from .kokoro import KokoroClient, KokoroError, measure_duration
from .textclean import SkipArticle, build_tts_input_from_article
from .wallabag import WallabagClient, WallabagError

logger = logging.getLogger(__name__)


def _resolve_voice(conn: sqlite3.Connection, settings: Settings) -> str:
    """Return the UI-tunable voice from the ``settings`` table.

    Falls back to ``settings.KOKORO_DEFAULT_VOICE`` when the row is missing
    or empty.
    """
    row = conn.execute("SELECT value FROM settings WHERE key='voice'").fetchone()
    if row is not None and row[0]:
        return str(row[0])
    return settings.KOKORO_DEFAULT_VOICE


async def generate_all(
    wallabag_client: WallabagClient,
    kokoro_client: KokoroClient,
    settings: Settings | None = None,
) -> dict:
    """Generate audio for all staged episodes, one at a time.

    Returns a summary dict ``{"total": N, "done": M, "failed": K, "skipped": L}``.
    A skipped article (cleaned text too short for TTS) counts as failed — its
    episode is marked ``failed`` with a ``"Skipped: ..."`` error and it is not
    recorded in processed_articles — and is additionally tracked in ``skipped``.
    Per-episode failures never abort the run.
    """
    settings = settings or get_settings()
    summary = {"total": 0, "done": 0, "failed": 0, "skipped": 0}

    conn = connect()
    try:
        staged = get_staged_episodes(conn)
        summary["total"] = len(staged)
        if not staged:
            return summary

        # All episodes in this run share one drive_id.
        drive_id = next_drive_id(conn)
        voice = _resolve_voice(conn, settings)

        audio_dir = settings.DATA_DIR / "audio"
        audio_dir.mkdir(parents=True, exist_ok=True)

        for ep in staged:
            episode_id = int(ep["id"])
            wallabag_id = int(ep["wallabag_id"])
            try:
                set_episode_generating(conn, episode_id)

                article = await wallabag_client.get_entry(wallabag_id)
                tts_text = build_tts_input_from_article(
                    article, min_chars=settings.MIN_TEXT_CHARS
                )
                audio_bytes = await kokoro_client.synthesize(tts_text, voice=voice)

                audio_path = audio_dir / f"{episode_id}.mp3"
                audio_path.write_bytes(audio_bytes)

                duration = measure_duration(audio_path)
                if duration is None:
                    # Unreadable/corrupt audio: fall back to the estimated
                    # reading time so the episode still gets a duration.
                    duration = int(ep["est_minutes"] or 0) * 60

                set_episode_done(conn, episode_id, str(audio_path), duration, drive_id)
                add_processed_article(conn, wallabag_id, episode_id)
                summary["done"] += 1
            except SkipArticle as exc:
                summary["skipped"] += 1
                summary["failed"] += 1
                logger.warning("Skipping episode %s: %s", episode_id, exc)
                set_episode_failed(conn, episode_id, f"Skipped: {exc}")
            except (KokoroError, WallabagError) as exc:
                summary["failed"] += 1
                logger.warning("Episode %s failed: %s", episode_id, exc)
                set_episode_failed(conn, episode_id, str(exc))
            except Exception:
                # Unexpected per-episode failure (disk, parsing, ...): record it
                # and keep going so one bad episode never stops the run.
                logger.exception("Unexpected error generating episode %s", episode_id)
                summary["failed"] += 1
                set_episode_failed(conn, episode_id, "Unexpected error")
    finally:
        conn.close()

    return summary