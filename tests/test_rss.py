"""Tests for the podcast RSS feed (app/rss.py) and the /feed.xml route.

The env fixture points DATA_DIR at tmp_path and initializes the DB schema,
matching the pattern used in test_pipeline.py. build_feed() reads settings via
get_settings() and opens the DB via connect(), so the fixture's env setup is
all that is needed.
"""

import sqlite3
import xml.etree.ElementTree as ET

import pytest

from app.config import get_settings
from app.db import get_db_path, init_db
from app.rss import build_feed

_REQUIRED_ENV = {
    "WALLABAG_CLIENT_ID": "test_client_id",
    "WALLABAG_CLIENT_SECRET": "test_client_secret",
    "WALLABAG_USERNAME": "test_user",
    "WALLABAG_PASSWORD": "test_pass",
}

_NS = {"itunes": "http://www.itunes.com/dtds/podcast-1.0.dtd"}


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Point DATA_DIR at tmp_path, init the DB schema, and seed settings."""
    for key, value in _REQUIRED_ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("BASE_URL", "http://podcast.example.test")
    get_settings.cache_clear()
    init_db(get_db_path())
    return tmp_path


def _insert_done(
    conn: sqlite3.Connection,
    wallabag_id: int,
    title: str,
    generated_at: str,
    audio_path: str | None = None,
    duration_sec: int = 60,
) -> None:
    """Insert a done episode with the given generated_at timestamp."""
    conn.execute(
        "INSERT INTO episodes (wallabag_id, title, source, url, status, "
        "est_minutes, language, audio_path, duration_sec, drive_id, "
        "created_at, generated_at) VALUES (?, ?, ?, ?, 'done', 5, 'en', ?, ?, "
        "1, '2026-01-01T00:00:00+00:00', ?)",
        (
            wallabag_id,
            title,
            f"example.com/{wallabag_id}",
            f"https://example.com/{wallabag_id}",
            audio_path,
            duration_sec,
            generated_at,
        ),
    )
    conn.commit()


def _feed_items(feed: bytes) -> list[ET.Element]:
    root = ET.fromstring(feed)
    assert root.tag == "rss"
    return root.findall("channel/item")


def test_valid_rss(env):
    with sqlite3.connect(get_db_path()) as conn:
        _insert_done(conn, 1, "First Article", "2026-01-01T00:00:00+00:00")
        _insert_done(conn, 2, "Second Article", "2026-01-02T00:00:00+00:00")

    feed = build_feed()

    root = ET.fromstring(feed)
    assert root.tag == "rss"
    assert root.get("version") == "2.0"
    channel = root.find("channel")
    assert channel.find("title").text == get_settings().FEED_TITLE
    # Channel-level iTunes podcast metadata.
    assert channel.find("itunes:category", _NS).get("text") == "Society & Culture"
    assert channel.find("itunes:explicit", _NS).text == "no"
    assert channel.find("itunes:type", _NS).text == "episodic"
    # Channel cover art: iTunes image plus the legacy RSS <image> element.
    base_url = get_settings().BASE_URL
    assert channel.find("itunes:image", _NS).get("href") == f"{base_url}/static/cover.png"
    assert channel.find("image").find("url").text == f"{base_url}/static/cover.png"
    items = channel.findall("item")
    assert len(items) == 2
    for item in items:
        assert item.find("title") is not None
        assert item.find("enclosure") is not None
        assert item.find("guid") is not None
        assert item.find("pubDate") is not None
        assert item.find("itunes:explicit", _NS).text == "no"
        assert item.find("itunes:episodeType", _NS).text == "full"
        assert item.find("itunes:image", _NS).get("href") == f"{base_url}/static/cover.png"


def test_description_includes_wallabag_link(env):
    with sqlite3.connect(get_db_path()) as conn:
        _insert_done(conn, 42, "My Article", "2026-01-01T00:00:00+00:00")

    items = _feed_items(build_feed())
    assert len(items) == 1
    desc = items[0].find("description").text
    assert "My Article" in desc
    assert "example.com/42" in desc
    wallabag_url = items[0].find("link").text
    expected_base = get_settings().WALLABAG_URL.rstrip("/")
    assert wallabag_url == f"{expected_base}/view/42"


def test_entry_link_points_to_wallabag(env):
    with sqlite3.connect(get_db_path()) as conn:
        _insert_done(conn, 7, "Linked Article", "2026-01-01T00:00:00+00:00")

    items = _feed_items(build_feed())
    link = items[0].find("link")
    assert link is not None
    expected_base = get_settings().WALLABAG_URL.rstrip("/")
    assert link.text == f"{expected_base}/view/7"


def test_enclosure_correctness(env):
    audio_dir = env / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    audio_path = audio_dir / "1.mp3"
    audio_path.write_bytes(b"FAKE_MP3" * 10)  # 80 bytes

    with sqlite3.connect(get_db_path()) as conn:
        _insert_done(
            conn, 1, "First Article", "2026-01-01T00:00:00+00:00",
            audio_path=str(audio_path),
        )

    root = ET.fromstring(build_feed())
    enclosure = root.find("channel/item/enclosure")
    base_url = get_settings().BASE_URL
    assert enclosure.get("url") == f"{base_url}/audio/1.mp3"
    assert enclosure.get("type") == "audio/mpeg"
    assert enclosure.get("length") == str(audio_path.stat().st_size)


def test_archived_excluded(env):
    with sqlite3.connect(get_db_path()) as conn:
        _insert_done(conn, 1, "One", "2026-01-01T00:00:00+00:00")
        _insert_done(conn, 2, "Two", "2026-01-02T00:00:00+00:00")
        conn.execute(
            "INSERT INTO episodes (wallabag_id, title, source, url, status, "
            "est_minutes, language, created_at, generated_at) VALUES "
            "(3, 'Archived', 'example.com/3', 'https://example.com/3', "
            "'archived', 5, 'en', '2026-01-01T00:00:00+00:00', "
            "'2026-01-03T00:00:00+00:00')"
        )
        conn.commit()

    items = _feed_items(build_feed())
    assert len(items) == 2
    assert "Archived" not in [item.find("title").text for item in items]


def test_newest_first(env):
    with sqlite3.connect(get_db_path()) as conn:
        _insert_done(conn, 1, "Oldest", "2026-01-01T00:00:00+00:00")
        _insert_done(conn, 2, "Middle", "2026-01-02T00:00:00+00:00")
        _insert_done(conn, 3, "Newest", "2026-01-03T00:00:00+00:00")

    items = _feed_items(build_feed())
    assert [item.find("title").text for item in items] == ["Newest", "Middle", "Oldest"]


def test_empty_feed(env):
    root = ET.fromstring(build_feed())
    assert root.tag == "rss"
    assert root.findall("channel/item") == []
    # The channel cover must be present even with zero episodes.
    channel = root.find("channel")
    base_url = get_settings().BASE_URL
    assert channel.find("itunes:image", _NS).get("href") == f"{base_url}/static/cover.png"
    assert channel.find("image").find("url").text == f"{base_url}/static/cover.png"


def test_missing_audio_and_bad_generated_at_are_graceful(env):
    """Missing audio files get enclosure length 0; bad generated_at falls back
    to the current time."""
    with sqlite3.connect(get_db_path()) as conn:
        _insert_done(
            conn, 1, "No Audio", "not-a-timestamp", audio_path="/nonexistent/1.mp3",
        )

    items = _feed_items(build_feed())
    assert len(items) == 1
    assert items[0].find("enclosure").get("length") == "0"
    assert items[0].find("pubDate") is not None


def test_feed_route(env):
    from fastapi.testclient import TestClient

    from app.main import app

    with sqlite3.connect(get_db_path()) as conn:
        _insert_done(conn, 1, "First Article", "2026-01-01T00:00:00+00:00")

    with TestClient(app) as client:
        response = client.get("/feed.xml")

    assert response.status_code == 200
    assert "xml" in response.headers["content-type"]
    root = ET.fromstring(response.content)
    assert root.tag == "rss"
    assert len(root.findall("channel/item")) == 1


def test_static_cover_served(env):
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as client:
        response = client.get("/static/cover.png")

    assert response.status_code == 200
    assert "image/png" in response.headers["content-type"]