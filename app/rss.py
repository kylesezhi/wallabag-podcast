"""Podcast RSS 2.0 feed generation (with iTunes extensions).

Builds a feed from done, non-archived episodes (newest generated first).
Audio files are referenced at ``{BASE_URL}/audio/{id}.mp3``. The channel and
each episode carry the cover art at ``{BASE_URL}/static/cover.png``.

Each podcast has its own feed addressed by GUID at
``{BASE_URL}/podcast/{guid}/feed.xml``: the podcast name is the channel title,
the alternate link points at the podcast hub page, and only that podcast's
done episodes are included. Calling :func:`build_feed` without a podcast
emits the legacy global feed at ``{BASE_URL}/feed.xml`` under ``FEED_TITLE``.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

from feedgen.feed import FeedGenerator

from .config import Settings, get_settings
from .db import connect, get_feed_episodes


def _parse_generated_at(value: str | None) -> datetime:
    """Parse an ISO-8601 ``generated_at`` string into a datetime.

    Falls back to the current UTC time when the value is missing or
    unparseable.
    """
    if not value:
        return datetime.now(timezone.utc)
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return datetime.now(timezone.utc)


def _enclosure_length(audio_path: str | None) -> int:
    """Return the audio file size in bytes, or 0 when missing/unreadable."""
    if not audio_path:
        return 0
    try:
        return os.path.getsize(audio_path)
    except OSError:
        return 0


def build_feed(
    podcast: dict | None = None, settings: Settings | None = None
) -> bytes:
    """Build a podcast RSS 2.0 feed and return it as UTF-8 XML bytes.

    The channel carries iTunes podcast metadata (category, explicit,
    type) and the show cover art (as an iTunes image plus the legacy RSS
    ``<image>`` element); each episode carries an iTunes duration, explicit
    flag, episode type, and the same cover art. Episodes whose audio file is
    missing still appear with enclosure length 0.

    When ``podcast`` is given (a record from :func:`app.db.create_podcast` or
    :func:`app.db.get_podcast_by_guid`), the channel is scoped to that
    podcast: its name becomes the title, the feed id and self link use the
    GUID-based URL ``{BASE_URL}/podcast/{guid}/feed.xml``, and only that
    podcast's episodes are included. Without a podcast the legacy global
    feed under ``FEED_TITLE`` is produced.
    """
    settings = settings or get_settings()

    fg = FeedGenerator()
    fg.load_extension("podcast")

    cover_url = f"{settings.BASE_URL}/static/cover.png"

    if podcast is None:
        feed_url = f"{settings.BASE_URL}/feed.xml"
        alternate_url = f"{settings.BASE_URL}/"
        channel_title = settings.FEED_TITLE
        podcast_id = None
    else:
        feed_url = f"{settings.BASE_URL}/podcast/{podcast['guid']}/feed.xml"
        alternate_url = f"{settings.BASE_URL}/podcast/{podcast['guid']}"
        channel_title = podcast["name"]
        podcast_id = podcast["id"]

    fg.id(feed_url)
    fg.title(channel_title)
    fg.link(href=feed_url, rel="self")
    fg.link(href=alternate_url, rel="alternate")
    fg.description("Podcast generated from Wallabag saved articles")
    fg.language("en")
    fg.podcast.itunes_category("Society & Culture")
    fg.podcast.itunes_explicit("no")
    fg.podcast.itunes_type("episodic")
    fg.podcast.itunes_image(cover_url)
    fg.image(cover_url, title=channel_title, link=alternate_url)

    conn = connect()
    try:
        episodes = get_feed_episodes(conn, podcast_id)
    finally:
        conn.close()

    for episode in episodes:
        # feedgen's add_entry() prepends by default; append so the newest-first
        # order returned by get_feed_episodes() is preserved in the feed.
        entry = fg.add_entry(order="append")
        wallabag_url = (
            f"{settings.WALLABAG_URL.rstrip('/')}/view/{episode['wallabag_id']}"
        )
        entry.title(episode["title"])
        delete_url = f"{settings.BASE_URL}/episode/{episode['id']}/delete"
        entry.description(
            f"{episode['title']} — from {episode['source']}"
            f"\n\nRead the original: {wallabag_url}"
            f"\n\nRemove from podcast: {delete_url}"
        )
        entry.content(
            f"<p>{episode['title']} — from {episode['source']}</p>"
            f'<p>Read the original: <a href="{wallabag_url}">{wallabag_url}</a></p>'
            f'<p>Remove from podcast: <a href="{delete_url}">{delete_url}</a></p>',
            type="CDATA",
        )
        entry.link(href=wallabag_url)
        entry.guid(f"{settings.BASE_URL}/audio/{episode['id']}.mp3")
        entry.pubDate(_parse_generated_at(episode["generated_at"]))
        entry.enclosure(
            url=f"{settings.BASE_URL}/audio/{episode['id']}.mp3",
            length=_enclosure_length(episode["audio_path"]),
            type="audio/mpeg",
        )
        entry.podcast.itunes_duration(int(episode["duration_sec"] or 0))
        entry.podcast.itunes_explicit("no")
        entry.podcast.itunes_episode_type("full")
        entry.podcast.itunes_image(cover_url)

    return fg.rss_str()