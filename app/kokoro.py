"""Kokoro TTS client (async, httpx) and audio duration measurement (mutagen).

Talks to a Kokoro-FastAPI server:
- ``GET  {base}/v1/audio/voices``  -> available voice ID strings
- ``POST {base}/v1/audio/speech``  -> synthesized audio bytes (mp3)

No real authentication is required; a dummy ``Authorization: Bearer not-needed``
header is sent for compatibility with servers that expect one.
"""

from __future__ import annotations

import io
import logging
from pathlib import Path

import httpx
import mutagen.mp3

from .config import Settings, get_settings

logger = logging.getLogger(__name__)

# Kokoro synthesis can take a while for long articles; keep a generous timeout.
_DEFAULT_TIMEOUT = 120.0


class KokoroError(Exception):
    """Base class for all Kokoro client errors."""


class KokoroConnectionError(KokoroError):
    """Could not reach the Kokoro server (connect / timeout)."""


class KokoroClient:
    """Async client for the Kokoro-FastAPI TTS server."""

    def __init__(
        self,
        settings: Settings | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._client = client
        self._owns_client = client is None

    # -- lifecycle ----------------------------------------------------------

    def _get_or_create_client(self) -> httpx.AsyncClient:
        """Return the injected client, or lazily create and cache our own."""
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=_DEFAULT_TIMEOUT,
                follow_redirects=True,
            )
            self._owns_client = True
        return self._client

    async def aclose(self) -> None:
        """Close the underlying httpx client (only if we created it)."""
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    # -- helpers ------------------------------------------------------------

    def _base_url(self) -> str:
        return self._settings.KOKORO_BASE_URL.rstrip("/")

    @staticmethod
    def _error_message(resp: httpx.Response) -> str:
        return f"Kokoro returned status {resp.status_code}: {resp.text[:200]}"

    # -- public API ---------------------------------------------------------

    async def voices(self) -> list[str]:
        """Return the available voice ID strings.

        Raises :class:`KokoroConnectionError` on network failure and
        :class:`KokoroError` on a non-2xx response.
        """
        url = f"{self._base_url()}/v1/audio/voices"
        client = self._get_or_create_client()
        try:
            resp = await client.get(url)
        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            raise KokoroConnectionError(
                f"Could not reach Kokoro at {url}: {exc}"
            ) from exc

        if not 200 <= resp.status_code < 300:
            raise KokoroError(self._error_message(resp))

        payload = resp.json()
        return [voice["id"] for voice in payload.get("voices", []) if voice.get("id")]

    async def synthesize(
        self,
        text: str,
        voice: str | None = None,
        speed: float | None = None,
    ) -> bytes:
        """Synthesize speech for ``text`` and return the raw audio bytes.

        Uses settings defaults for voice/speed/response_format when not given.
        Raises :class:`KokoroConnectionError` on network failure and
        :class:`KokoroError` on a non-2xx response.
        """
        url = f"{self._base_url()}/v1/audio/speech"
        body = {
            "model": "kokoro",
            "input": text,
            "voice": voice if voice is not None else self._settings.KOKORO_DEFAULT_VOICE,
            "response_format": self._settings.KOKORO_RESPONSE_FORMAT,
            "speed": speed if speed is not None else self._settings.KOKORO_SPEED,
        }
        client = self._get_or_create_client()
        try:
            resp = await client.post(
                url,
                json=body,
                headers={"Authorization": "Bearer not-needed"},
            )
        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            raise KokoroConnectionError(
                f"Could not reach Kokoro at {url}: {exc}"
            ) from exc

        if not 200 <= resp.status_code < 300:
            raise KokoroError(self._error_message(resp))

        return resp.content


def measure_duration(audio: Path | bytes) -> int | None:
    """Return the audio duration in whole seconds via mutagen.

    Accepts raw MP3 bytes (a single synthesis chunk) or a path to an MP3 file.
    Returns None when the audio can't be parsed (empty, corrupt, or not
    actually MP3). Never raises, so generation never crashes on bad audio.
    """
    try:
        if isinstance(audio, bytes):
            length = mutagen.mp3.MP3(io.BytesIO(audio)).info.length
        else:
            length = mutagen.mp3.MP3(audio).info.length
        return int(length)
    except Exception:
        return None
