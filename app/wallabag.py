"""Wallabag REST API client (async, httpx).

Handles OAuth2 password-grant authentication with automatic token refresh,
paginated enumeration of unread article metadata (lightweight, no content),
and retrieval of a single full article including HTML content.

All API calls go through :meth:`WallabagClient._request`, which attaches the
Bearer token and transparently refreshes it once on a 401 before retrying.
Network-level failures are surfaced as :class:`WallabagConnectionError` and
authentication/authorization failures as :class:`WallabagAuthError`.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

import httpx

from .config import Settings, get_settings

logger = logging.getLogger(__name__)

# Safety margin (seconds) subtracted from the token lifetime so we refresh
# before Wallabag actually rejects the token.
_TOKEN_SKEW_SECONDS = 60


class WallabagError(Exception):
    """Base class for all Wallabag client errors."""


class WallabagAuthError(WallabagError):
    """Authentication or authorization failed (bad credentials / token)."""


class WallabagConnectionError(WallabagError):
    """Could not reach the Wallabag server (connect / timeout)."""


@dataclass(frozen=True, slots=True)
class ArticleMeta:
    """Lightweight unread-article metadata (no HTML content)."""

    id: int
    title: str
    url: str
    domain_name: str
    reading_time: int
    language: str | None
    tags: list[str]  # normalized to lowercase strings
    is_archived: bool
    is_starred: bool


@dataclass(frozen=True, slots=True)
class ArticleFull:
    """A full article: metadata plus the raw HTML content."""

    id: int
    title: str
    url: str
    domain_name: str
    reading_time: int
    language: str | None
    tags: list[str]  # normalized to lowercase strings
    is_archived: bool
    is_starred: bool
    content: str  # raw HTML


def _normalize_tags(raw: Any) -> list[str]:
    """Normalize the Wallabag ``tags`` field into lowercase strings.

    Wallabag may return tags either as a list of objects
    ``[{"id":1,"slug":"x","title":"Foo"}]`` or as a list of plain strings
    (``["Computer", "Tech"]``). Handle both defensively, lowercasing the
    title/slug/string and dropping any empty entries.
    """
    if not raw:
        return []
    tags: list[str] = []
    for tag in raw:
        if isinstance(tag, dict):
            value = tag.get("title") or tag.get("slug") or tag.get("label")
        elif isinstance(tag, str):
            value = tag
        else:
            value = str(tag)
        value = (value or "").strip().lower()
        if value:
            tags.append(value)
    return tags


class WallabagClient:
    """Async client for the Wallabag REST API."""

    def __init__(
        self,
        settings: Settings | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._client = client
        self._owns_client = client is None

        # In-memory token cache.
        self._access_token: str | None = None
        self._refresh_token: str | None = None
        self._expires_at: float = 0.0

    # -- lifecycle ----------------------------------------------------------

    def _get_or_create_client(self) -> httpx.AsyncClient:
        """Return the injected client, or lazily create and cache our own."""
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=30.0,
                follow_redirects=True,
            )
            self._owns_client = True
        return self._client

    async def aclose(self) -> None:
        """Close the underlying httpx client (only if we created it)."""
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    # -- token handling -----------------------------------------------------

    def _token_is_valid(self) -> bool:
        return bool(self._access_token) and time.monotonic() < self._expires_at

    async def _request_token(self, params: dict[str, str]) -> None:
        """Exchange credentials for a token and cache it.

        Raises :class:`WallabagAuthError` if the token endpoint rejects us.
        """
        url = f"{self._settings.WALLABAG_URL.rstrip('/')}/oauth/v2/token"
        client = self._get_or_create_client()
        try:
            resp = await client.post(url, data=params)
        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            raise WallabagConnectionError(
                f"Could not reach Wallabag token endpoint {url}: {exc}"
            ) from exc

        if resp.status_code >= 300:
            raise WallabagAuthError(
                f"Wallabag token request failed with status {resp.status_code}: "
                f"{resp.text[:200]}"
            )

        payload = resp.json()
        self._access_token = payload.get("access_token")
        self._refresh_token = payload.get("refresh_token")
        expires_in = int(payload.get("expires_in", 0))
        self._expires_at = time.monotonic() + max(expires_in, 0) - _TOKEN_SKEW_SECONDS

        if not self._access_token:
            raise WallabagAuthError(
                "Wallabag token response contained no access_token"
            )

    async def _password_grant(self) -> None:
        await self._request_token(
            {
                "grant_type": "password",
                "client_id": self._settings.WALLABAG_CLIENT_ID,
                "client_secret": self._settings.WALLABAG_CLIENT_SECRET,
                "username": self._settings.WALLABAG_USERNAME,
                "password": self._settings.WALLABAG_PASSWORD,
            }
        )

    async def _refresh_grant(self) -> None:
        await self._request_token(
            {
                "grant_type": "refresh_token",
                "client_id": self._settings.WALLABAG_CLIENT_ID,
                "client_secret": self._settings.WALLABAG_CLIENT_SECRET,
                "refresh_token": self._refresh_token or "",
            }
        )

    async def _get_token(self) -> str:
        """Return a valid access token, obtaining one if necessary."""
        if self._token_is_valid():
            return self._access_token  # type: ignore[return-value]

        if self._refresh_token:
            try:
                await self._refresh_grant()
                return self._access_token  # type: ignore[return-value]
            except WallabagAuthError:
                # Fall back to a fresh password grant if the refresh token is
                # expired/revoked.
                logger.info("Wallabag refresh failed; re-authenticating")
                await self._password_grant()
        else:
            await self._password_grant()

        return self._access_token  # type: ignore[return-value]

    # -- request plumbing ---------------------------------------------------

    async def _request(
        self,
        method: str,
        url: str,
        **kwargs: Any,
    ) -> httpx.Response:
        """Perform an authenticated request with one 401 refresh+retry."""
        token = await self._get_token()
        headers = dict(kwargs.pop("headers", {}))
        headers["Authorization"] = f"Bearer {token}"

        client = self._get_or_create_client()
        try:
            resp = await client.request(method, url, headers=headers, **kwargs)
        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            raise WallabagConnectionError(
                f"Could not reach Wallabag at {url}: {exc}"
            ) from exc

        if resp.status_code == 401:
            # Token expired between fetch and use: refresh once and retry once.
            self._access_token = None
            self._refresh_token = None
            token = await self._get_token()
            headers["Authorization"] = f"Bearer {token}"
            try:
                resp = await client.request(method, url, headers=headers, **kwargs)
            except (httpx.ConnectError, httpx.TimeoutException) as exc:
                raise WallabagConnectionError(
                    f"Could not reach Wallabag at {url}: {exc}"
                ) from exc
            if resp.status_code == 401:
                raise WallabagAuthError(
                    f"Wallabag rejected authorization at {url} after refresh "
                    f"(status {resp.status_code})"
                )

        return resp

    # -- public API ---------------------------------------------------------

    async def test_connection(self) -> bool:
        """Probe connectivity/auth and return True on success."""
        try:
            token = await self._get_token()
            return bool(token)
        except WallabagError:
            return False

    async def list_unread_metadata(self, per_page: int = 100) -> list[ArticleMeta]:
        """Enumerate unread article metadata, paginated.

        Fetches at most ``settings.MAX_FETCH_PAGES`` pages of ``per_page``
        items. Only lightweight metadata is requested (``detail=metadata``,
        which omits the HTML content).
        """
        base = self._settings.WALLABAG_URL.rstrip("/")
        max_pages = self._settings.MAX_FETCH_PAGES
        items: list[ArticleMeta] = []

        page = 1
        total_pages: int | None = None
        while True:
            if page > max_pages:
                logger.warning(
                    "Stopped Wallabag enumeration at MAX_FETCH_PAGES=%s", max_pages
                )
                break

            url = (
                f"{base}/api/entries.json?archive=0&detail=metadata&page={page}"
                f"&perPage={per_page}&sort=created&order=asc"
            )
            resp = await self._request("GET", url)
            payload = resp.json()

            embedded = payload.get("_embedded") or {}
            raw_items = embedded.get("items") or []
            items.extend(self._parse_meta(item) for item in raw_items)

            if total_pages is None:
                total_pages = int(payload.get("pages", 1) or 1)
            if page >= total_pages or not raw_items:
                break
            page += 1

        return items

    async def get_entry(self, entry_id: int) -> ArticleFull:
        """Fetch a single full article including its HTML content."""
        base = self._settings.WALLABAG_URL.rstrip("/")
        url = f"{base}/api/entries/{entry_id}.json"
        resp = await self._request("GET", url)
        payload = resp.json()
        return self._parse_full(payload)

    # -- parsing helpers ----------------------------------------------------

    @staticmethod
    def _parse_meta(item: dict[str, Any]) -> ArticleMeta:
        return ArticleMeta(
            id=int(item.get("id") or 0),
            title=str(item.get("title") or ""),
            url=str(item.get("url") or ""),
            domain_name=str(item.get("domain_name") or ""),
            reading_time=int(item.get("reading_time") or 0),
            language=item.get("language"),
            tags=_normalize_tags(item.get("tags")),
            is_archived=bool(int(item.get("is_archived") or 0)),
            is_starred=bool(int(item.get("is_starred") or 0)),
        )

    @classmethod
    def _parse_full(cls, item: dict[str, Any]) -> ArticleFull:
        meta = cls._parse_meta(item)
        return ArticleFull(
            id=meta.id,
            title=meta.title,
            url=meta.url,
            domain_name=meta.domain_name,
            reading_time=meta.reading_time,
            language=meta.language,
            tags=meta.tags,
            is_archived=meta.is_archived,
            is_starred=meta.is_starred,
            content=str(item.get("content") or ""),
        )
