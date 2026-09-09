"""Tests for the podcast repository functions and per-podcast scoping."""

import re
import sqlite3

import pytest

from app.config import get_settings
from app.db import (
    create_podcast,
    delete_podcast,
    get_db_path,
    get_newest_podcast,
    get_podcast_by_guid,
    get_podcasts,
    get_queue_episodes,
    get_staged_episodes,
    has_staged_episodes,
    init_db,
    insert_staged_episode,
    reset_failed_to_staged,
)
from app.naming import ADJECTIVES, ANIMALS, OBJECTS, PLACES, SCIENCE

_REQUIRED_ENV = {
    "WALLABAG_CLIENT_ID": "test_client_id",
    "WALLABAG_CLIENT_SECRET": "test_client_secret",
    "WALLABAG_USERNAME": "test_user",
    "WALLABAG_PASSWORD": "test_pass",
}

_VALID_WORDS = {
    word
    for pool in (ADJECTIVES, ANIMALS, OBJECTS, SCIENCE)
    for entry in pool
    for word in entry.split()
} | {word for place in PLACES for word in place.lower().split()}


@pytest.fixture
def db(tmp_path, monkeypatch):
    """Point DATA_DIR at tmp_path and init the DB schema."""
    for key, value in _REQUIRED_ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    get_settings.cache_clear()
    init_db(get_db_path())
    return tmp_path


@pytest.fixture
def conn(db):
    with sqlite3.connect(get_db_path()) as connection:
        yield connection


def _podcast_id(conn, podcast: dict) -> int:
    return podcast["id"]


def _insert_episode(conn, podcast_id, wallabag_id, status, **extra):
    columns = ["wallabag_id", "status", "podcast_id"]
    values = [wallabag_id, status, podcast_id]
    for key, value in extra.items():
        columns.append(key)
        values.append(value)
    placeholders = ", ".join("?" for _ in values)
    conn.execute(
        f"INSERT INTO episodes ({', '.join(columns)}) VALUES ({placeholders})",
        values,
    )
    conn.commit()


def test_create_podcast_returns_valid_record(conn):
    podcast = create_podcast(conn)
    assert re.fullmatch(r"[0-9a-f]{8}", podcast["guid"])
    assert podcast["name"] == podcast["name"].title()
    assert len(podcast["name"].split()) >= 2
    for word in podcast["name"].lower().split():
        assert word in _VALID_WORDS
    assert podcast["created_at"]
    assert podcast["id"] is not None


def test_create_podcast_generates_distinct_names_and_guids(conn):
    podcasts = [create_podcast(conn) for _ in range(10)]
    guids = {p["guid"] for p in podcasts}
    names = {p["name"] for p in podcasts}
    assert len(guids) == 10
    assert len(names) == 10


def test_get_podcast_by_guid_hit_and_miss(conn):
    podcast = create_podcast(conn)
    found = get_podcast_by_guid(conn, podcast["guid"])
    assert found["id"] == podcast["id"]
    assert found["name"] == podcast["name"]
    assert get_podcast_by_guid(conn, "ffffffff") is None


def test_get_newest_podcast(conn):
    podcasts = [create_podcast(conn) for _ in range(3)]
    assert get_newest_podcast(conn)["id"] == podcasts[-1]["id"]


def test_get_newest_podcast_none_when_empty(db):
    with sqlite3.connect(get_db_path()) as conn:
        first = conn.execute("SELECT id FROM podcasts").fetchone()[0]
        delete_podcast(conn, first)
        assert get_newest_podcast(conn) is None


def test_get_podcasts_aggregates(conn):
    podcast_a = create_podcast(conn)
    podcast_b = create_podcast(conn)
    _insert_episode(conn, podcast_a["id"], 1, "staged", est_minutes=5)
    _insert_episode(conn, podcast_a["id"], 2, "staged", est_minutes=10)
    _insert_episode(conn, podcast_a["id"], 3, "done", duration_sec=3600)
    _insert_episode(conn, podcast_a["id"], 4, "failed")
    _insert_episode(conn, podcast_b["id"], 5, "done", duration_sec=1800)

    podcasts = get_podcasts(conn)
    # Newest first: podcast_b (created after podcast_a) precedes podcast_a.
    assert podcasts[0]["id"] == podcast_b["id"]
    assert podcasts[1]["id"] == podcast_a["id"]

    by_id = {p["id"]: p for p in podcasts}
    a = by_id[podcast_a["id"]]
    assert a["staged"] == 2
    assert a["generating"] == 0
    assert a["done"] == 1
    assert a["failed"] == 1
    assert a["staged_minutes"] == 15
    assert a["done_seconds"] == 3600

    b = by_id[podcast_b["id"]]
    assert b["done"] == 1
    assert b["staged"] == 0
    assert b["failed"] == 0
    assert b["staged_minutes"] == 0
    assert b["done_seconds"] == 1800


def test_get_podcasts_zero_episode_podcast(conn):
    podcast = create_podcast(conn)
    podcasts = get_podcasts(conn)
    row = next(p for p in podcasts if p["id"] == podcast["id"])
    assert row["staged"] == 0
    assert row["done"] == 0
    assert row["failed"] == 0
    assert row["staged_minutes"] == 0
    assert row["done_seconds"] == 0


def test_scoped_queries(conn):
    podcast_a = create_podcast(conn)
    podcast_b = create_podcast(conn)
    _insert_episode(conn, podcast_a["id"], 1, "staged", est_minutes=5)
    _insert_episode(conn, podcast_a["id"], 2, "done", duration_sec=100)
    _insert_episode(conn, podcast_b["id"], 3, "staged", est_minutes=7)

    staged_a = get_staged_episodes(conn, podcast_id=podcast_a["id"])
    assert [e["wallabag_id"] for e in staged_a] == [1]
    staged_b = get_staged_episodes(conn, podcast_id=podcast_b["id"])
    assert [e["wallabag_id"] for e in staged_b] == [3]

    queue_a = get_queue_episodes(conn, podcast_id=podcast_a["id"])
    assert [e["wallabag_id"] for e in queue_a] == [1, 2]

    assert has_staged_episodes(conn, podcast_id=podcast_a["id"]) is True
    assert has_staged_episodes(conn, podcast_id=podcast_b["id"]) is True

    # Unscoped (podcast_id=None) still returns everything.
    all_staged = get_staged_episodes(conn)
    assert {e["wallabag_id"] for e in all_staged} == {1, 3}
    all_queue = get_queue_episodes(conn)
    assert {e["wallabag_id"] for e in all_queue} == {1, 2, 3}


def test_reset_failed_to_staged_scoped(conn):
    podcast_a = create_podcast(conn)
    podcast_b = create_podcast(conn)
    _insert_episode(conn, podcast_a["id"], 1, "failed", error="boom")
    _insert_episode(conn, podcast_a["id"], 2, "failed", error="boom")
    _insert_episode(conn, podcast_b["id"], 3, "failed", error="boom")

    count = reset_failed_to_staged(conn, podcast_id=podcast_a["id"])
    assert count == 2

    assert {
        e["wallabag_id"] for e in get_staged_episodes(conn, podcast_id=podcast_a["id"])
    } == {1, 2}
    # Podcast B's failed rows are untouched.
    failed_b = conn.execute(
        "SELECT wallabag_id FROM episodes WHERE podcast_id=? AND status='failed'",
        (podcast_b["id"],),
    ).fetchall()
    assert [row[0] for row in failed_b] == [3]


def test_insert_staged_episode_with_podcast_id(conn):
    podcast = create_podcast(conn)
    insert_staged_episode(
        conn, 100, "Title", "example.com", "https://example.com/100", 5, "en",
        podcast_id=podcast["id"],
    )
    staged = get_staged_episodes(conn, podcast_id=podcast["id"])
    assert [e["wallabag_id"] for e in staged] == [100]
    row = conn.execute(
        "SELECT podcast_id FROM episodes WHERE wallabag_id=100"
    ).fetchone()
    assert row[0] == podcast["id"]


def test_delete_podcast(conn):
    podcast_a = create_podcast(conn)
    podcast_b = create_podcast(conn)
    _insert_episode(conn, podcast_a["id"], 1, "done",
                    audio_path="/tmp/1.mp3", duration_sec=10)
    _insert_episode(conn, podcast_a["id"], 2, "done",
                    audio_path="/tmp/2.mp3", duration_sec=20)
    _insert_episode(conn, podcast_a["id"], 3, "staged")
    _insert_episode(conn, podcast_b["id"], 4, "done",
                    audio_path="/tmp/4.mp3", duration_sec=30)
    for wallabag_id in (1, 2, 4):
        conn.execute(
            "INSERT INTO processed_articles (wallabag_id, processed_at) VALUES (?, ?)",
            (wallabag_id, "2026-01-01T00:00:00+00:00"),
        )
    conn.commit()

    result = delete_podcast(conn, podcast_a["id"])
    assert result is not None
    assert result["name"] == podcast_a["name"]
    assert result["guid"] == podcast_a["guid"]
    assert result["episode_count"] == 3
    assert set(result["audio_paths"]) == {"/tmp/1.mp3", "/tmp/2.mp3"}
    assert set(result["wallabag_ids"]) == {1, 2, 3}

    # A's rows + processed rows gone.
    assert conn.execute(
        "SELECT COUNT(*) FROM episodes WHERE podcast_id=?", (podcast_a["id"],)
    ).fetchone()[0] == 0
    assert conn.execute(
        "SELECT COUNT(*) FROM processed_articles WHERE wallabag_id IN (1, 2)"
    ).fetchone()[0] == 0
    assert get_podcast_by_guid(conn, podcast_a["guid"]) is None

    # B's episode + processed rows survive.
    assert conn.execute(
        "SELECT COUNT(*) FROM episodes WHERE podcast_id=?", (podcast_b["id"],)
    ).fetchone()[0] == 1
    assert conn.execute(
        "SELECT COUNT(*) FROM processed_articles WHERE wallabag_id=4"
    ).fetchone()[0] == 1
    assert get_podcast_by_guid(conn, podcast_b["guid"]) is not None
    assert any(p["id"] == podcast_b["id"] for p in get_podcasts(conn))

    assert delete_podcast(conn, 99999) is None