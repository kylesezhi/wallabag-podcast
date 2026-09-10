"""Tests for per-podcast cover art (app/covers.py) and its route.

The env fixture points DATA_DIR at tmp_path and initializes the DB schema,
matching the pattern used in test_rss.py / test_web.py. Cover rendering reads
the pristine static/cover.png from the repo (unaffected by DATA_DIR).
"""

import io

import pytest
from PIL import Image

from app import covers
from app.config import get_settings
from app.db import connect, get_db_path, get_newest_podcast, init_db

_REQUIRED_ENV = {
    "WALLABAG_CLIENT_ID": "test_client_id",
    "WALLABAG_CLIENT_SECRET": "test_client_secret",
    "WALLABAG_USERNAME": "test_user",
    "WALLABAG_PASSWORD": "test_pass",
    "WALLABAG_URL": "https://wallabag.test",
}


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Point DATA_DIR at tmp_path and init the DB schema (auto-creates one
    podcast)."""
    for key, value in _REQUIRED_ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    get_settings.cache_clear()
    init_db(get_db_path())
    return tmp_path


@pytest.fixture
def client(env):
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as test_client:
        yield test_client


def _podcast() -> dict:
    """Return the newest podcast (auto-created by init_db)."""
    conn = connect()
    try:
        podcast = get_newest_podcast(conn)
    finally:
        conn.close()
    assert podcast is not None
    return podcast


def _white_pixels_in_band(png: bytes, top: int, bottom: int) -> int:
    """Count near-white pixels in a horizontal band of a PNG."""
    im = Image.open(io.BytesIO(png)).convert("RGB")
    width, height = im.size
    count = 0
    for x in range(0, width, 2):
        for y in range(top, bottom, 2):
            r, g, b = im.getpixel((x, y))
            if r > 200 and g > 200 and b > 200:
                count += 1
    return count


# ---------------------------------------------------------------------------
# Renderer
# ---------------------------------------------------------------------------


def test_render_cover_png_white_text_in_bottom_band():
    png = covers.render_cover_png("Quantum Quokka")

    # White glyphs appear inside the bottom band (the near-black strip where
    # the name is drawn); the pristine base has essentially none there.
    count = _white_pixels_in_band(png, 1254 - 160, 1254 - 60)
    assert count > 500


def test_render_cover_png_autofit_shrinks_long_name():
    short_font, _ = covers._pick_font("Quantum Quokka")
    long_font, _ = covers._pick_font("Shopping Cart Gas Station")

    assert short_font.size == 72
    assert long_font.size < 72
    assert long_font.size >= 16
    # A long name still renders with white glyphs in the band (one line).
    png = covers.render_cover_png("Shopping Cart Gas Station")
    assert _white_pixels_in_band(png, 1254 - 160, 1254 - 60) > 500


def test_render_cover_png_deterministic():
    first = covers.render_cover_png("Quantum Quokka")
    second = covers.render_cover_png("Quantum Quokka")

    assert first == second


# ---------------------------------------------------------------------------
# Route
# ---------------------------------------------------------------------------


def test_cover_route_serves_png(client):
    podcast = _podcast()

    response = client.get(f"/podcast/{podcast['guid']}/cover.png")

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.headers["cache-control"] == "public, max-age=86400"
    # The served PNG differs from the pristine base (name overlay present).
    assert response.content != covers.base_cover_bytes()


def test_cover_route_caches_identical_bytes(client):
    podcast = _podcast()

    first = client.get(f"/podcast/{podcast['guid']}/cover.png")
    second = client.get(f"/podcast/{podcast['guid']}/cover.png")

    assert first.content == second.content
    assert covers.get_cached(podcast["guid"]) == first.content


def test_cover_route_unknown_guid_404(client):
    response = client.get("/podcast/ffffffff/cover.png")

    assert response.status_code == 404


def test_cover_route_fallback_on_render_failure(client, monkeypatch):
    podcast = _podcast()

    def boom(name):
        raise OSError("font exploded")

    monkeypatch.setattr(covers, "render_cover_png", boom)

    response = client.get(f"/podcast/{podcast['guid']}/cover.png")

    # A failed render degrades gracefully to the pristine base cover.
    assert response.status_code == 200
    assert response.content == covers.base_cover_bytes()
    # Failures are not cached: the next request retries the render.
    assert covers.get_cached(podcast["guid"]) is None


def test_cover_route_delete_invalidates_cache(client):
    podcast = _podcast()
    client.get(f"/podcast/{podcast['guid']}/cover.png")
    assert covers.get_cached(podcast["guid"]) is not None

    response = client.post(
        f"/podcast/{podcast['guid']}/delete", follow_redirects=False
    )

    assert response.status_code == 303
    assert covers.get_cached(podcast["guid"]) is None