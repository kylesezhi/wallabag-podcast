"""Tests for the Wallabag API client using httpx.MockTransport."""

import httpx
import pytest

from app.config import Settings
from app.wallabag import (
    ArticleFull,
    ArticleMeta,
    WallabagAuthError,
    WallabagClient,
    WallabagConnectionError,
    WallabagError,
)

WALLABAG_URL = "https://wallabag.example.test"
CLIENT_ID = "test_client_id"
CLIENT_SECRET = "test_client_secret"
USERNAME = "test_user"
PASSWORD = "test_pass"

ACCESS_TOKEN = "access-token-123"
NEW_ACCESS_TOKEN = "new-access-token-456"
REFRESH_TOKEN = "refresh-token-abc"


def _settings(**overrides) -> Settings:
    defaults = {
        "WALLABAG_CLIENT_ID": CLIENT_ID,
        "WALLABAG_CLIENT_SECRET": CLIENT_SECRET,
        "WALLABAG_USERNAME": USERNAME,
        "WALLABAG_PASSWORD": PASSWORD,
        "WALLABAG_URL": WALLABAG_URL,
    }
    defaults.update(overrides)
    return Settings(_env_file=None, **defaults)


def _token_response(access=ACCESS_TOKEN, refresh=REFRESH_TOKEN) -> dict:
    return {
        "access_token": access,
        "expires_in": 3600,
        "refresh_token": refresh,
        "token_type": "bearer",
        "scope": None,
    }


def _make_client(handler, settings=None) -> WallabagClient:
    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)
    return WallabagClient(settings=settings or _settings(), client=client)


def _meta_item(entry_id: int, title: str = None) -> dict:
    return {
        "id": entry_id,
        "title": title or f"Article {entry_id}",
        "url": f"https://example.com/{entry_id}",
        "domain_name": "example.com",
        "reading_time": 5,
        "language": "en",
        "tags": [],
        "is_archived": 0,
        "is_starred": 0,
        "preview_picture": "",
        "created_at": "2024-01-01T00:00:00+00:00",
        "updated_at": "2024-01-01T00:00:00+00:00",
    }


def _entry_payload(items, page: int, pages: int, total: int, per_page: int) -> dict:
    return {
        "_embedded": {"items": items},
        "_links": {"self": {"href": "..."}},
        "limit": per_page,
        "page": page,
        "pages": pages,
        "total": total,
    }


# ---------------------------------------------------------------------------
# 1. password grant + pagination
# ---------------------------------------------------------------------------


def test_password_grant_and_pagination():
    token_bodies = []
    entry_queries = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth/v2/token":
            token_bodies.append(request.content.decode())
            return httpx.Response(200, json=_token_response())

        if request.url.path == "/api/entries.json":
            entry_queries.append(request.url.params)
            page = int(request.url.params["page"])
            items = [_meta_item(i) for i in range((page - 1) * 2 + 1, page * 2 + 1)]
            return httpx.Response(
                200,
                json=_entry_payload(
                    items=items, page=page, pages=3, total=6, per_page=2
                ),
            )

        return httpx.Response(404)

    client = _make_client(handler)

    async def _go():
        return await client.list_unread_metadata(per_page=2)

    result = asyncio_run(_go())

    # flattening + pagination
    assert [a.id for a in result] == [1, 2, 3, 4, 5, 6]
    # token endpoint hit exactly once (password grant), form-encoded
    assert len(token_bodies) == 1
    body = token_bodies[0]
    assert "grant_type=password" in body
    assert f"client_id={CLIENT_ID}" in body
    assert f"username={USERNAME}" in body
    # query strings include archive=0 & detail=metadata
    for params in entry_queries:
        assert params["archive"] == "0"
        assert params["detail"] == "metadata"
    assert len(entry_queries) == 3


# ---------------------------------------------------------------------------
# 2. MAX_FETCH_PAGES cap
# ---------------------------------------------------------------------------


def test_max_fetch_pages_cap():
    entry_queries = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth/v2/token":
            return httpx.Response(200, json=_token_response())
        if request.url.path == "/api/entries.json":
            entry_queries.append(request.url.params)
            page = int(request.url.params["page"])
            items = [_meta_item(i) for i in range((page - 1) * 10 + 1, page * 10 + 1)]
            # Server claims 999 pages; cap should stop us early.
            return httpx.Response(
                200,
                json=_entry_payload(
                    items=items, page=page, pages=999, total=9990, per_page=10
                ),
            )
        return httpx.Response(404)

    settings = _settings(MAX_FETCH_PAGES=2)
    client = _make_client(handler, settings=settings)

    async def _go():
        return await client.list_unread_metadata(per_page=10)

    result = asyncio_run(_go())

    assert len(entry_queries) == 2
    assert [a.id for a in result] == list(range(1, 21))


# ---------------------------------------------------------------------------
# 3. 401 refresh + retry
# ---------------------------------------------------------------------------


def test_401_refresh_and_retry():
    grant_types = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth/v2/token":
            body = request.content.decode()
            if "grant_type=password" in body:
                grant_types.append("password")
                return httpx.Response(200, json=_token_response())
            if "grant_type=refresh_token" in body:
                grant_types.append("refresh_token")
                return httpx.Response(
                    200, json=_token_response(access=NEW_ACCESS_TOKEN)
                )
            return httpx.Response(400, text="bad grant")

        if request.url.path == "/api/entries.json":
            auth = request.headers.get("Authorization", "")
            # First entries call uses the stale token -> 401.
            if auth == f"Bearer {ACCESS_TOKEN}":
                return httpx.Response(401, json={"error": "expired_token"})
            # Retry uses the NEW bearer.
            assert auth == f"Bearer {NEW_ACCESS_TOKEN}"
            items = [_meta_item(1)]
            return httpx.Response(
                200,
                json=_entry_payload(
                    items=items, page=1, pages=1, total=1, per_page=100
                ),
            )

        return httpx.Response(404)

    client = _make_client(handler)

    async def _go():
        # Force a stale access token in the cache.
        client._access_token = ACCESS_TOKEN
        client._refresh_token = REFRESH_TOKEN
        client._expires_at = 0  # expired
        return await client.list_unread_metadata()

    result = asyncio_run(_go())

    assert [a.id for a in result] == [1]
    # No password grant needed: a refresh token was pre-seeded, and the 401 on
    # the entries call triggered a single refresh-token grant.
    assert grant_types == ["refresh_token"]


def test_second_401_raises_auth_error():
    grant_types = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth/v2/token":
            body = request.content.decode()
            if "grant_type=password" in body:
                grant_types.append("password")
                return httpx.Response(200, json=_token_response())
            if "grant_type=refresh_token" in body:
                grant_types.append("refresh_token")
                return httpx.Response(
                    200, json=_token_response(access=NEW_ACCESS_TOKEN)
                )
            return httpx.Response(400, text="bad grant")

        if request.url.path == "/api/entries.json":
            # Always reject with 401, even with the new bearer -> second 401.
            return httpx.Response(401, json={"error": "invalid_token"})

        return httpx.Response(404)

    client = _make_client(handler)

    async def _go():
        client._access_token = ACCESS_TOKEN
        client._refresh_token = REFRESH_TOKEN
        client._expires_at = 0  # expired
        await client.list_unread_metadata()

    with pytest.raises(WallabagAuthError):
        asyncio_run(_go())

    # Initial fetch refreshes via the seeded refresh token; on the entries 401
    # the refresh token is cleared, so the retry obtains a fresh token via a
    # password grant, then the second 401 raises AuthError.
    assert grant_types == ["refresh_token", "password"]


# ---------------------------------------------------------------------------
# 4. get_entry (full content)
# ---------------------------------------------------------------------------


def test_get_entry_returns_full_content():
    full_item = _meta_item(42, title="Deep Dive")
    full_item["content"] = "<p>Hello <b>world</b></p>"
    full_item["tags"] = [{"id": 1, "slug": "ai", "title": "AI"}]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth/v2/token":
            return httpx.Response(200, json=_token_response())
        if request.url.path == "/api/entries/42.json":
            return httpx.Response(200, json=full_item)
        return httpx.Response(404)

    client = _make_client(handler)

    async def _go():
        return await client.get_entry(42)

    entry: ArticleFull = asyncio_run(_go())

    assert entry.id == 42
    assert entry.title == "Deep Dive"
    assert entry.url == "https://example.com/42"
    assert entry.domain_name == "example.com"
    assert entry.reading_time == 5
    assert entry.language == "en"
    assert entry.content == "<p>Hello <b>world</b></p>"
    assert entry.tags == ["ai"]
    assert entry.is_archived is False
    assert entry.is_starred is False


# ---------------------------------------------------------------------------
# 4b. archive (PATCH archive=1)
# ---------------------------------------------------------------------------


def test_archive_sends_patch_with_archive_field():
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth/v2/token":
            return httpx.Response(200, json=_token_response())
        if request.url.path == "/api/entries/42.json":
            requests.append(request)
            return httpx.Response(200, json=_meta_item(42))
        return httpx.Response(404)

    client = _make_client(handler)

    async def _go():
        await client.archive(42)

    asyncio_run(_go())

    assert len(requests) == 1
    request = requests[0]
    assert request.method == "PATCH"
    assert "Authorization" in request.headers
    assert "archive=1" in request.content.decode()


def test_archive_non_2xx_raises():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth/v2/token":
            return httpx.Response(200, json=_token_response())
        if request.method == "PATCH":
            return httpx.Response(500, text="boom")
        return httpx.Response(404)

    client = _make_client(handler)

    async def _go():
        await client.archive(7)

    with pytest.raises(WallabagError):
        asyncio_run(_go())


# ---------------------------------------------------------------------------
# 5. tag normalization (object + string forms)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ([{"id": 1, "slug": "x", "title": "Foo"}], ["foo"]),
        (["Computer", "Tech"], ["computer", "tech"]),
        ([{"title": "Mixed Case"}, {"slug": "tech-news"}], ["mixed case", "tech-news"]),
        ([], []),
        (None, []),
    ],
)
def test_tag_normalization(raw, expected):
    item = _meta_item(1)
    item["tags"] = raw

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth/v2/token":
            return httpx.Response(200, json=_token_response())
        if request.url.path == "/api/entries/1.json":
            return httpx.Response(200, json=item)
        return httpx.Response(404)

    client = _make_client(handler)

    async def _go():
        return await client.get_entry(1)

    entry = asyncio_run(_go())
    assert entry.tags == expected


# ---------------------------------------------------------------------------
# 6. auth failure (token endpoint 401)
# ---------------------------------------------------------------------------


def test_auth_failure_raises_auth_error():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth/v2/token":
            return httpx.Response(401, json={"error": "invalid_grant"})
        return httpx.Response(404)

    client = _make_client(handler)

    async def _go():
        await client.list_unread_metadata()

    with pytest.raises(WallabagAuthError):
        asyncio_run(_go())


def test_test_connection_returns_false_on_auth_failure():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth/v2/token":
            return httpx.Response(401, json={"error": "invalid_grant"})
        return httpx.Response(404)

    client = _make_client(handler)

    async def _go():
        return await client.test_connection()

    assert asyncio_run(_go()) is False


# ---------------------------------------------------------------------------
# 7. connection error
# ---------------------------------------------------------------------------


def test_connection_error_raises_connection_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    client = _make_client(handler)

    async def _go():
        await client.list_unread_metadata()

    with pytest.raises(WallabagConnectionError):
        asyncio_run(_go())


def test_test_connection_returns_false_on_connection_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    client = _make_client(handler)

    async def _go():
        return await client.test_connection()

    assert asyncio_run(_go()) is False


# -- helpers ----------------------------------------------------------------
import asyncio


def asyncio_run(coro):
    return asyncio.run(coro)
