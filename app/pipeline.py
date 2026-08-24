"""Queue orchestration: article selection, queue operations, and generation.

- ``add_random`` pulls unread Wallabag metadata, filters exclusions, and
  stages N random episodes.
- ``delete_item`` / ``clear_queue`` manage the queue; deleting an episode
  first archives its article in Wallabag (aborting on failure), and deleting
  a done episode also removes its audio file and processed_articles row.
- ``stats`` summarizes the queue for the UI.
- ``generate_all()`` produces one MP3 per staged episode: fetch the full
  Wallabag entry, clean the HTML into TTS input, synthesize with Kokoro,
  write the audio under ``DATA_DIR/audio/{id}.mp3``, measure its duration, and
  mark the episode ``done`` (recording a processed_articles row). Failures are
  isolated per episode: a bad article marks that episode ``failed`` and the run
  continues with the next one.
"""

from __future__ import annotations

import asyncio
import logging
import random
import sqlite3
from pathlib import Path

from .config import Settings, get_settings
from .db import (
    add_processed_article,
    connect,
    delete_episode,
    delete_processed_article,
    delete_staged_failed_episodes,
    get_episode_status,
    get_episode_wallabag_id,
    get_processed_wallabag_ids,
    get_staged_episodes,
    get_staged_wallabag_ids,
    get_stats_rows,
    insert_staged_episode,
    next_drive_id,
    set_episode_done,
    set_episode_failed,
    set_episode_generating,
)
from .kokoro import KokoroClient, KokoroError, measure_duration
from .textclean import SkipArticle, build_tts_input_from_article
from .wallabag import WallabagClient, WallabagError

logger = logging.getLogger(__name__)


async def add_random(
    n: int,
    wallabag_client: WallabagClient,
    settings: Settings | None = None,
) -> int:
    """Fetch unread Wallabag metadata, filter exclusions + already-known
    articles, pick ``n`` at random, and insert them as ``staged`` episodes.

    An article is excluded if any of its tags is in ``settings.EXCLUDE_TAGS``
    (case-insensitive) or if its wallabag_id is already known — either in
    ``processed_articles`` (successfully generated before) or on any existing
    episode row. Returns the number actually staged (may be < n).
    """
    settings = settings or get_settings()
    exclude = {tag.lower() for tag in settings.EXCLUDE_TAGS}
    if n <= 0:
        return 0

    conn = connect()
    try:
        # Already-known wallabag_ids: processed before OR already in the queue.
        existing = get_staged_wallabag_ids(conn) | get_processed_wallabag_ids(conn)

        metas = await wallabag_client.list_unread_metadata()

        candidates = [
            m
            for m in metas
            if m.id not in existing and not (set(m.tags) & exclude)
        ]

        if len(candidates) <= n:
            chosen = candidates
        else:
            chosen = random.sample(candidates, n)

        count = 0
        for m in chosen:
            insert_staged_episode(
                conn, m.id, m.title, m.domain_name, m.url, m.reading_time, m.language
            )
            count += 1
        return count
    finally:
        conn.close()


async def delete_item(episode_id: int, wallabag_client: WallabagClient) -> None:
    """Archive the article in Wallabag, then delete the episode locally.

    The Wallabag archive call happens FIRST: if it fails (connection, auth,
    or API error) a ``WallabagError`` propagates and nothing is deleted
    locally. On success the staged|failed|generating|done episode row is
    deleted; for done episodes local cleanup also unlinks the mp3 at
    audio_path (best-effort) and removes the processed_articles dedupe row.
    Raises ValueError if not found or non-deletable (archived).
    """
    conn = connect()
    try:
        status = get_episode_status(conn, episode_id)
        if status is None:
            raise ValueError(f"Episode {episode_id} not found")
        if status not in ("staged", "failed", "generating", "done"):
            raise ValueError(
                f"Episode {episode_id} is '{status}', cannot delete "
                "(only staged|failed|generating|done)"
            )
        wallabag_id = get_episode_wallabag_id(conn, episode_id)
        # Archive before deleting anything locally so a failed API call
        # leaves the queue untouched (abort-on-failure contract).
        await wallabag_client.archive(wallabag_id)

        deleted = delete_episode(conn, episode_id)
        if deleted is None:
            raise ValueError(f"Episode {episode_id} not found")
        _, audio_path, _ = deleted
        if status == "done":
            # Remove the dedupe row first so the article stays consistent
            # even if the mp3 unlink fails (best-effort disk cleanup).
            delete_processed_article(conn, wallabag_id)
            if audio_path:
                try:
                    Path(audio_path).unlink(missing_ok=True)
                except OSError:
                    pass
    finally:
        conn.close()


def clear_queue() -> int:
    """Delete staged|failed episodes. Returns count deleted.

    Does NOT touch done/archived episodes or their processed_articles rows.
    """
    conn = connect()
    try:
        return delete_staged_failed_episodes(conn)
    finally:
        conn.close()


def stats() -> dict:
    """Return queue statistics: minutes, article count, status counts, drive_id.

    ``total_minutes`` = sum(est_minutes for staged) + sum(duration_sec // 60
    for done). ``articles`` counts the active queue (staged + done + failed +
    generating, excluding archived). ``drive_id`` is the most recent done
    episode's drive_id, or None.
    """
    conn = connect()
    try:
        rows = get_stats_rows(conn)
        counts = rows["status_counts"]
        staged = int(counts.get("staged", 0))
        done = int(counts.get("done", 0))
        failed = int(counts.get("failed", 0))
        generating = int(counts.get("generating", 0))
        return {
            "total_minutes": rows["staged_minutes"] + rows["done_seconds"] // 60,
            "articles": staged + done + failed + generating,
            "staged": staged,
            "done": done,
            "failed": failed,
            "generating": generating,
            "archived": int(counts.get("archived", 0)),
            "drive_id": rows["done_drive_id"],
        }
    finally:
        conn.close()


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
    Per-episode failures never abort the run. A task cancellation
    (``task.cancel()``) DOES abort the run: the in-flight episode is marked
    ``failed`` ("Cancelled by user"), the remaining staged episodes stay
    ``staged``, and the partial summary is returned normally (the exception is
    not re-raised).
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
            except asyncio.CancelledError:
                # The run was cancelled via task.cancel(): CancelledError is a
                # BaseException, so the except Exception clause below would not
                # catch it. Halt the entire run — the in-flight episode is
                # marked failed (visible, retryable, removable) and the
                # remaining staged episodes stay staged. Do NOT re-raise: the
                # caller awaits this task and expects the partial summary, and
                # _run_generation's finally flips the generating flag.
                summary["failed"] += 1
                logger.warning("Episode %s cancelled by user", episode_id)
                set_episode_failed(conn, episode_id, "Cancelled by user")
                break
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