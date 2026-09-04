"""Tests for the Kokoro TTS client using httpx.MockTransport."""

import json
from pathlib import Path

import httpx
import pytest

from app.config import Settings
from app.kokoro import KokoroClient, KokoroConnectionError, KokoroError, measure_duration

KOKORO_URL = "http://kokoro.example.test"


def _settings(**overrides) -> Settings:
    defaults = {
        "WALLABAG_CLIENT_ID": "test_client_id",
        "WALLABAG_CLIENT_SECRET": "test_client_secret",
        "WALLABAG_USERNAME": "test_user",
        "WALLABAG_PASSWORD": "test_pass",
        "KOKORO_BASE_URL": KOKORO_URL,
        "KOKORO_DEFAULT_VOICE": "af_heart",
        "KOKORO_SPEED": 1.0,
        "KOKORO_RESPONSE_FORMAT": "mp3",
    }
    defaults.update(overrides)
    return Settings(_env_file=None, **defaults)


def _make_client(handler, settings=None) -> KokoroClient:
    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)
    return KokoroClient(settings=settings or _settings(), client=client)


async def test_voices_returns_voice_dicts():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/audio/voices"
        return httpx.Response(
            200,
            json={
                "voices": [
                    {"id": "af_heart", "name": "Heart"},
                    {"id": "am_michael", "name": "Michael"},
                    {"id": "bm_daniel", "name": "Daniel"},
                    {"id": "ef_dora", "name": "Dora"},
                ]
            },
        )

    client = _make_client(handler)
    result = await client.voices()
    assert result == [
        {"id": "af_heart", "label": "Heart, Female - American English"},
        {"id": "am_michael", "label": "Michael, Male - American English"},
        {"id": "bm_daniel", "label": "Daniel, Male - British English"},
        {"id": "ef_dora", "label": "Dora, Female - Spanish"},
    ]


async def test_synthesize_returns_audio_bytes():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/audio/speech"
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, content=b"FAKE_MP3_DATA")

    client = _make_client(handler)
    result = await client.synthesize("Hello world", voice="am_michael", speed=1.5)

    assert result == b"FAKE_MP3_DATA"
    assert captured["body"] == {
        "model": "kokoro",
        "input": "Hello world",
        "voice": "am_michael",
        "response_format": "mp3",
        "speed": 1.5,
    }


async def test_synthesize_uses_settings_defaults():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, content=b"FAKE_MP3_DATA")

    client = _make_client(handler)
    await client.synthesize("Hello")

    assert captured["body"]["voice"] == "af_heart"
    assert captured["body"]["speed"] == 1.0
    assert captured["body"]["response_format"] == "mp3"


async def test_connection_error_raises_kokoro_connection_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    client = _make_client(handler)

    with pytest.raises(KokoroConnectionError):
        await client.voices()

    with pytest.raises(KokoroConnectionError):
        await client.synthesize("Hello")


async def test_non_2xx_raises_kokoro_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="internal error")

    client = _make_client(handler)

    with pytest.raises(KokoroError):
        await client.voices()

    with pytest.raises(KokoroError):
        await client.synthesize("Hello")


def test_measure_duration_returns_seconds(monkeypatch):
    class _FakeInfo:
        length = 42.7

    class _FakeMP3:
        def __init__(self, path):
            self.path = path
            self.info = _FakeInfo()

    monkeypatch.setattr("mutagen.mp3.MP3", _FakeMP3)
    assert measure_duration(Path("/tmp/fake.mp3")) == 42


def test_measure_duration_returns_none_on_parse_error(monkeypatch):
    def _boom(path):
        raise ValueError("not an mp3")

    monkeypatch.setattr("mutagen.mp3.MP3", _boom)
    assert measure_duration(Path("/tmp/fake.mp3")) is None