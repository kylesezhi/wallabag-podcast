"""Tests for the Pocket TTS client using httpx.MockTransport.

The mock handlers return REAL WAV bytes (built with the stdlib ``wave``
module, 24 kHz mono 16-bit like Pocket TTS's output) so the client's WAV
decode + lameenc MP3 re-encode runs for real; the resulting MP3 is verified
with mutagen. No network access is needed.
"""

import io
import logging
import re
import struct
import wave
from pathlib import Path

import httpx
import mutagen.mp3
import pytest

import app.tts as tts_module
from app.config import Settings
from app.tts import (
    BUILTIN_VOICES,
    TTSClient,
    TTSConnectionError,
    TTSError,
    encode_mp3,
    encode_silence,
    measure_duration,
    measure_duration_seconds,
    wav_to_mp3,
)

TTS_URL = "http://tts.example.test"


def _settings(**overrides) -> Settings:
    defaults = {
        "WALLABAG_CLIENT_ID": "test_client_id",
        "WALLABAG_CLIENT_SECRET": "test_client_secret",
        "WALLABAG_USERNAME": "test_user",
        "WALLABAG_PASSWORD": "test_pass",
        "TTS_BASE_URL": TTS_URL,
        "TTS_DEFAULT_VOICE": "charles",
    }
    defaults.update(overrides)
    return Settings(_env_file=None, **defaults)


def _make_client(handler, settings=None) -> TTSClient:
    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)
    return TTSClient(settings=settings or _settings(), client=client)


def _form_fields(request: httpx.Request) -> dict[str, str]:
    """Decode the multipart/form-data text fields of an httpx request."""
    content_type = request.headers["content-type"]
    boundary = content_type.partition("boundary=")[2].strip('"').encode()
    fields: dict[str, str] = {}
    for section in request.content.split(b"--" + boundary):
        section = section.strip(b"\r\n")
        if not section or section == b"--":
            continue
        raw_headers, _, raw_value = section.partition(b"\r\n\r\n")
        match = re.search(rb'name="([^"]+)"', raw_headers)
        assert match is not None, f"multipart part without a name: {raw_headers!r}"
        fields[match.group(1).decode()] = raw_value.decode("utf-8")
    return fields


def _wav_bytes(
    frames: int = 240,
    sample_rate: int = 24000,
    channels: int = 1,
    sample: int = 0,
) -> bytes:
    """Build a tiny real WAV the way ``pocket-tts serve`` would emit it.

    Defaults mimic the service: mono, 16-bit PCM, 24 kHz, a few hundred
    frames of (near-)silence.
    """
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(struct.pack("<h", sample) * frames * channels)
    return buf.getvalue()


def _placeholder_header_wav() -> bytes:
    """A WAV whose RIFF/data size fields are the server's 1e9-frame garbage.

    Mirrors the unseekable-stream behavior of ``pocket-tts serve``: the fmt
    chunk is correct, but the RIFF size and data chunk size claim
    ``1_000_000_000`` frames' worth of bytes while only 240 real samples
    follow.
    """
    fmt = struct.pack("<HHIIHH", 1, 1, 24000, 24000 * 2, 2, 16)
    pcm = b"\x00\x00" * 240
    placeholder_bytes = 1_000_000_000 * 2
    body = b"fmt " + struct.pack("<I", len(fmt)) + fmt
    body += b"data" + struct.pack("<I", placeholder_bytes) + pcm
    return b"RIFF" + struct.pack("<I", 4 + len(body)) + b"WAVE" + body


def _fmt_payload(
    channels: int = 1, sample_rate: int = 24000, bits: int = 16
) -> bytes:
    """A canonical 16-byte PCM ``fmt `` chunk body."""
    return struct.pack(
        "<HHIIHH",
        1,  # PCM
        channels,
        sample_rate,
        sample_rate * channels * bits // 8,
        channels * bits // 8,
        bits,
    )


def _raw_wav(fmt: bytes, data: bytes = b"", extra_chunks: bytes = b"") -> bytes:
    """Assemble raw RIFF/WAVE bytes from a fmt body and a raw data payload.

    Unlike :func:`_wav_bytes` (which goes through the stdlib ``wave`` writer)
    this builds the chunk layout by hand so tests can craft malformed or
    unusual headers.
    """
    body = b"fmt " + struct.pack("<I", len(fmt)) + fmt
    body += extra_chunks
    body += b"data" + struct.pack("<I", len(data)) + data
    return b"RIFF" + struct.pack("<I", 4 + len(body)) + b"WAVE" + body


# ---------------------------------------------------------------------------
# synthesize: request shape
# ---------------------------------------------------------------------------


async def test_synthesize_sends_multipart_text_and_voice_url():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/tts"
        captured["fields"] = _form_fields(request)
        captured["content_type"] = request.headers["content-type"]
        # The Pocket TTS service has no authentication; no dummy header.
        assert "authorization" not in {k.lower() for k in request.headers}
        return httpx.Response(200, content=_wav_bytes())

    client = _make_client(handler)
    result = await client.synthesize("Hello world", voice="jane")

    assert captured["content_type"].startswith("multipart/form-data")
    assert captured["fields"] == {"text": "Hello world", "voice_url": "jane"}
    # The WAV response is decoded and re-encoded as MP3 with the module's
    # shared lameenc parameters.
    assert result == wav_to_mp3(_wav_bytes())


async def test_synthesize_uses_settings_default_voice():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["fields"] = _form_fields(request)
        return httpx.Response(200, content=_wav_bytes())

    client = _make_client(handler)
    await client.synthesize("Hello")

    assert captured["fields"]["voice_url"] == "charles"
    assert captured["fields"]["text"] == "Hello"


async def test_synthesize_strips_pause_tokens():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["fields"] = _form_fields(request)
        return httpx.Response(200, content=_wav_bytes())

    client = _make_client(handler)
    await client.synthesize("[pause:0.5s] Intro. [pause:1s] Body [pause:0.5s]")

    text = captured["fields"]["text"]
    assert "[pause:" not in text
    assert "Intro." in text and "Body" in text


# ---------------------------------------------------------------------------
# synthesize: WAV -> MP3 conversion
# ---------------------------------------------------------------------------


async def test_synthesize_returns_mutagen_parseable_mp3():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=_wav_bytes(frames=2400))

    client = _make_client(handler)
    result = await client.synthesize("Hello world")

    parsed = mutagen.mp3.MP3(io.BytesIO(result))
    assert parsed.info.sample_rate == 24000
    assert parsed.info.channels == 1
    assert parsed.info.bitrate == 64000
    assert parsed.info.length > 0


def test_wav_to_mp3_roundtrips_real_wav_bytes():
    result = wav_to_mp3(_wav_bytes(frames=2400))

    parsed = mutagen.mp3.MP3(io.BytesIO(result))
    assert parsed.info.sample_rate == 24000
    assert parsed.info.channels == 1
    assert parsed.info.bitrate == 64000


def test_wav_to_mp3_preserves_non_silent_audio():
    """A mute regression would be invisible against all-silence fixtures.

    A loud alternating ±full-scale waveform is decoded and re-encoded and
    the result is verified independently of ``wav_to_mp3``.

    The REAL discriminator is (b): at CBR, mutagen's duration is derived
    from file size, so it cannot distinguish silent from non-silent audio —
    (a) is only a parseability sanity check. The byte-difference against
    ``encode_silence`` of the same length proves the waveform actually
    survived the round-trip.
    """
    frames = 12000  # 0.5 s at 24 kHz
    pcm = (struct.pack("<h", 32767) + struct.pack("<h", -32767)) * (frames // 2)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(24000)
        wav.writeframes(pcm)

    mp3 = wav_to_mp3(buf.getvalue())

    # (a) Parseability sanity only: one LAME frame is 48 ms at 24 kHz, and
    # LAME's encoder delay/padding plus mutagen's size-derived CBR length
    # measure ~52 ms over for a 0.5 s fixture.
    parsed = mutagen.mp3.MP3(io.BytesIO(mp3))
    assert parsed.info.sample_rate == 24000
    assert abs(parsed.info.length - 0.5) <= 0.06
    # (b) The actual mute-regression check: non-silent input must not
    # collapse into the silence encoding.
    assert mp3 != encode_silence(0.5)


def test_wav_to_mp3_tolerates_placeholder_chunk_sizes():
    """The server streams WAV with bogus RIFF/data sizes; decode must not care.

    The hand-built placeholder-header WAV carries the same PCM as a
    well-formed ``wave``-module WAV, so both must encode to identical MP3.
    """
    assert wav_to_mp3(_placeholder_header_wav()) == wav_to_mp3(_wav_bytes())


def test_wav_to_mp3_rejects_non_wav_payload():
    with pytest.raises(TTSError, match="not RIFF/WAVE"):
        wav_to_mp3(b"FAKE_MP3")


def test_wav_to_mp3_rejects_non_pcm_encoding():
    fmt = struct.pack("<HHIIHH", 3, 1, 24000, 24000 * 4, 4, 32)  # IEEE float
    body = b"fmt " + struct.pack("<I", len(fmt)) + fmt
    body += b"data" + struct.pack("<I", 8) + b"\x00" * 8
    wav = b"RIFF" + struct.pack("<I", 4 + len(body)) + b"WAVE" + body

    with pytest.raises(TTSError, match="not uncompressed PCM"):
        wav_to_mp3(wav)


def test_wav_to_mp3_rejects_non_16bit_samples():
    fmt = struct.pack("<HHIIHH", 1, 1, 24000, 24000, 1, 8)  # 8-bit PCM
    body = b"fmt " + struct.pack("<I", len(fmt)) + fmt
    body += b"data" + struct.pack("<I", 8) + b"\x00" * 8
    wav = b"RIFF" + struct.pack("<I", 4 + len(body)) + b"WAVE" + body

    with pytest.raises(TTSError, match="16-bit"):
        wav_to_mp3(wav)


def test_wav_to_mp3_rejects_missing_data_chunk():
    fmt = struct.pack("<HHIIHH", 1, 1, 24000, 24000 * 2, 2, 16)
    body = b"fmt " + struct.pack("<I", len(fmt)) + fmt
    wav = b"RIFF" + struct.pack("<I", 4 + len(body)) + b"WAVE" + body

    with pytest.raises(TTSError, match="no data chunk"):
        wav_to_mp3(wav)


# ---------------------------------------------------------------------------
# WAV parser branches (hand-rolled RIFF reader)
# ---------------------------------------------------------------------------


def test_wav_to_mp3_rejects_one_byte_mono_payload():
    """A 1-byte mono data chunk holds no complete 16-bit frame: reject it."""
    wav = _raw_wav(_fmt_payload(channels=1), data=b"\x00")

    with pytest.raises(TTSError, match="no audio samples"):
        wav_to_mp3(wav)


def test_wav_to_mp3_rejects_partial_stereo_frame():
    """A 3-byte stereo data chunk holds no complete 4-byte frame: reject it."""
    wav = _raw_wav(_fmt_payload(channels=2), data=b"\x00\x00\x00")

    with pytest.raises(TTSError, match="no audio samples"):
        wav_to_mp3(wav)


def test_wav_to_mp3_rejects_zero_payload_data_chunk():
    wav = _raw_wav(_fmt_payload(channels=1), data=b"")

    with pytest.raises(TTSError, match="no audio samples"):
        wav_to_mp3(wav)


def test_wav_to_mp3_rejects_truncated_fmt_chunk():
    """A fmt chunk declaring fewer than 16 bytes cannot be parsed."""
    short_fmt = struct.pack("<HHI", 1, 1, 24000)  # 8 bytes, below the minimum
    wav = _raw_wav(short_fmt, data=b"\x00\x00" * 240)

    with pytest.raises(TTSError, match="fmt chunk is truncated"):
        wav_to_mp3(wav)


def test_wav_to_mp3_rejects_fmt_chunk_truncated_in_buffer():
    """A fmt chunk declaring 16 bytes but supplying fewer also cannot parse.

    Complements :func:`test_wav_to_mp3_rejects_truncated_fmt_chunk`, which
    covers ``declared < 16``; this one declares a full 16-byte fmt but the
    buffer ends mid-body (the stream was cut), which must hit the second
    half of the same guard.
    """
    partial_fmt = _fmt_payload()[:10]  # a real fmt start, cut mid-chunk
    body = b"fmt " + struct.pack("<I", 16) + partial_fmt  # declares 16, supplies 10
    wav = b"RIFF" + struct.pack("<I", 4 + len(body)) + b"WAVE" + body

    with pytest.raises(TTSError, match="fmt chunk is truncated"):
        wav_to_mp3(wav)


def test_wav_to_mp3_skips_unknown_chunks():
    """An unknown (LIST) chunk between fmt and data is skipped, not fatal.

    The LIST body carries a NON-zero pattern: if the parser mis-skipped the
    chunk, those bytes would land in the PCM slice and change the encoded
    MP3, so byte-identical output vs the same PCM without the LIST chunk
    proves the skip rather than just parseability.
    """
    list_chunk = b"LIST" + struct.pack("<I", 8) + b"INFOXXXX"
    wav = _raw_wav(_fmt_payload(), data=b"\x00\x00" * 240, extra_chunks=list_chunk)

    assert wav_to_mp3(wav) == wav_to_mp3(_wav_bytes(frames=240))


def test_wav_to_mp3_skips_odd_sized_unknown_chunk_with_padding():
    """Odd-sized unknown chunks carry a pad byte the parser must step over.

    RIFF pads odd chunk bodies to a word boundary. Any mis-step in the
    ``declared & 1`` pad arithmetic shifts the PCM slice (or loses the data
    chunk entirely), changing the encoding — byte-identical output vs the
    clean WAV proves both the skip and the padding handling.
    """
    odd_list = (
        b"LIST" + struct.pack("<I", 7) + b"INFOXXX" + b"\x00"  # 7-byte body + pad
    )
    wav = _raw_wav(_fmt_payload(), data=b"\x00\x00" * 240, extra_chunks=odd_list)

    assert wav_to_mp3(wav) == wav_to_mp3(_wav_bytes(frames=240))


def test_wav_to_mp3_reads_params_from_extended_fmt_chunk():
    """A fmt chunk longer than 16 bytes (cbSize extension) still decodes.

    The parser reads channels/rate/bits from the first 16 bytes and skips
    the remaining declared fmt bytes before hunting for the data chunk.
    """
    extended_fmt = _fmt_payload() + struct.pack("<H", 0)  # cbSize = 0
    assert len(extended_fmt) == 18
    pcm = b"\x00\x00" * 240
    wav = _raw_wav(extended_fmt, data=pcm)

    parsed = mutagen.mp3.MP3(io.BytesIO(wav_to_mp3(wav)))
    assert parsed.info.sample_rate == 24000
    assert parsed.info.channels == 1


def test_wav_to_mp3_truncates_odd_data_to_frame_boundary():
    """A stray trailing byte is dropped before encoding, not fed to lameenc.

    The 4801-byte mono payload trims to 4800 bytes (2400 whole frames =
    0.2 s), so it encodes byte-identically to the clean fixture and the
    measured duration reflects only whole frames.
    """
    odd = _raw_wav(_fmt_payload(), data=b"\x00\x00" * 4800 + b"\x00")

    mp3 = wav_to_mp3(odd)
    assert mp3 == wav_to_mp3(_wav_bytes(frames=4800))
    measured = measure_duration_seconds(mp3)
    assert measured is not None
    assert abs(measured - 0.2) <= 0.1


def test_wav_to_mp3_encodes_stereo_channel_count():
    """The encoder honors the WAV's channel count instead of assuming mono."""
    result = wav_to_mp3(_wav_bytes(frames=480, channels=2))

    parsed = mutagen.mp3.MP3(io.BytesIO(result))
    assert parsed.info.channels == 2
    assert parsed.info.sample_rate == 24000
    assert parsed.info.bitrate == 64000


async def test_synthesize_warns_once_on_unexpected_sample_rate(
    caplog, monkeypatch
):
    """Silence stays at 24 kHz, so an unexpected speech rate is flagged once."""
    monkeypatch.setattr(tts_module, "_rate_mismatch_warned", False)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=_wav_bytes(sample_rate=16000))

    client = _make_client(handler)
    with caplog.at_level(logging.WARNING, logger="app.tts"):
        await client.synthesize("Hello")
        await client.synthesize("Hello again")

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "16000" in warnings[0].getMessage()


# ---------------------------------------------------------------------------
# synthesize: error mapping
# ---------------------------------------------------------------------------


async def test_connection_error_raises_tts_connection_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    client = _make_client(handler)

    with pytest.raises(TTSConnectionError):
        await client.synthesize("Hello")


async def test_timeout_raises_tts_connection_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("too slow")

    client = _make_client(handler)

    with pytest.raises(TTSConnectionError):
        await client.synthesize("Hello")


async def test_non_2xx_raises_tts_error_with_status():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="internal error")

    client = _make_client(handler)

    with pytest.raises(TTSError, match="500"):
        await client.synthesize("Hello")


async def test_error_message_includes_body_excerpt():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"detail": "Text must not be empty"})

    client = _make_client(handler)

    with pytest.raises(TTSError, match="Text must not be empty"):
        await client.synthesize("")


async def test_undecodable_wav_raises_tts_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"not audio at all")

    client = _make_client(handler)

    with pytest.raises(TTSError, match="not RIFF/WAVE"):
        await client.synthesize("Hello")


# ---------------------------------------------------------------------------
# silence generation
# ---------------------------------------------------------------------------


def test_encode_silence_returns_parseable_mp3():
    mp3 = encode_silence(1.0)

    parsed = mutagen.mp3.MP3(io.BytesIO(mp3))
    assert parsed.info.sample_rate == 24000
    assert parsed.info.channels == 1
    assert parsed.info.bitrate == 64000


def test_encode_silence_duration_matches_request():
    """Measured duration tracks the request within ~two MP3 frames.

    lameenc's encoder delay/padding frames add ~50 ms beyond the encoded
    samples and mutagen derives CBR length from file size, so 1 s of silence
    measures a touch over 1.0 s — close enough for audible pauses.
    """
    for seconds in (0.5, 1.0, 2.0):
        measured = measure_duration_seconds(encode_silence(seconds))
        assert measured is not None
        assert abs(measured - seconds) <= 0.1


def test_encode_silence_is_cached_per_duration():
    # lru_cache hands back the identical bytes object for repeated requests.
    assert encode_silence(0.5) is encode_silence(0.5)
    assert encode_silence(1.0) is encode_silence(1.0)
    assert encode_silence(0.5) != encode_silence(1.0)


def test_encode_silence_fractional_seconds():
    measured = measure_duration_seconds(encode_silence(0.25))
    assert measured is not None
    assert abs(measured - 0.25) <= 0.1


def test_encode_silence_non_positive_returns_empty():
    assert encode_silence(0) == b""
    assert encode_silence(-1.5) == b""


def test_encode_silence_matches_speech_encoding_parameters():
    """Silence and speech share framing so concatenated playback is seamless.

    24000 samples of silence must encode exactly like 24000 samples of
    zero-valued speech PCM.
    """
    assert encode_silence(1.0) == encode_mp3(b"\x00\x00" * 24000, 24000, 1)


# ---------------------------------------------------------------------------
# built-in voices + transitional helpers
# ---------------------------------------------------------------------------


def test_builtin_voices_contract():
    """Lock the exact set of the 21 English built-in Pocket TTS voices."""
    expected_ids = {
        "alba",
        "anna",
        "azelma",
        "bill_boerst",
        "caro_davy",
        "charles",
        "cosette",
        "eponine",
        "eve",
        "fantine",
        "george",
        "jane",
        "jean",
        "javert",
        "marius",
        "mary",
        "michael",
        "paul",
        "peter_yearsley",
        "stuart_bell",
        "vera",
    }
    ids = [v["id"] for v in BUILTIN_VOICES]
    assert len(ids) == 21
    assert len(set(ids)) == len(ids)
    assert set(ids) == expected_ids
    for voice in BUILTIN_VOICES:
        assert set(voice) == {"id", "label"}
        assert voice["id"] == voice["id"].lower()
        assert voice["id"].replace("_", " ").strip()
        assert voice["label"].strip()


async def test_health_true_on_200():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/health"
        return httpx.Response(200, json={"status": "healthy"})

    client = _make_client(handler)
    assert await client.health() is True


async def test_health_false_on_non_2xx():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="unavailable")

    client = _make_client(handler)
    assert await client.health() is False


async def test_health_false_on_connect_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    client = _make_client(handler)
    assert await client.health() is False


async def test_health_false_on_malformed_base_url():
    """health() never raises — an unparsable TTS_BASE_URL is just False.

    ``http://[::1`` is an invalid IPv6 literal: httpx raises
    ``httpx.InvalidURL`` while building the request, before any transport
    is consulted. The call counter proves the probe never reached a
    transport (a bare garbage string like "not a url" IS normalized by
    httpx into a relative path and would reach the transport).
    """
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        return httpx.Response(200, json={"status": "healthy"})

    client = _make_client(handler, settings=_settings(TTS_BASE_URL="http://[::1"))
    assert await client.health() is False
    assert calls["count"] == 0


async def test_health_false_when_transport_raises_invalid_url():
    """Even a transport-level InvalidURL comes back as a plain False.

    ``httpx.InvalidURL`` is a bare Exception (not a ``RequestError``), so
    this pins the widened catch that keeps health probes from raising.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.InvalidURL("malformed URL surfaced at request time")

    client = _make_client(handler)
    assert await client.health() is False


# ---------------------------------------------------------------------------
# duration measurement (mutagen) — never raises
# ---------------------------------------------------------------------------


def test_measure_duration_returns_seconds(monkeypatch):
    class _FakeInfo:
        length = 42.7

    class _FakeMP3:
        def __init__(self, path):
            self.path = path
            self.info = _FakeInfo()

    monkeypatch.setattr("mutagen.mp3.MP3", _FakeMP3)
    assert measure_duration(Path("/tmp/fake.mp3")) == 42


def test_measure_duration_seconds_returns_float(monkeypatch):
    class _FakeInfo:
        length = 42.7

    class _FakeMP3:
        def __init__(self, path):
            self.path = path
            self.info = _FakeInfo()

    monkeypatch.setattr("mutagen.mp3.MP3", _FakeMP3)
    assert measure_duration_seconds(Path("/tmp/fake.mp3")) == 42.7


def test_measure_duration_seconds_returns_none_on_parse_error(monkeypatch):
    def _boom(path):
        raise ValueError("not an mp3")

    monkeypatch.setattr("mutagen.mp3.MP3", _boom)
    assert measure_duration_seconds(Path("/tmp/fake.mp3")) is None


def test_measure_duration_returns_none_on_parse_error(monkeypatch):
    def _boom(path):
        raise ValueError("not an mp3")

    monkeypatch.setattr("mutagen.mp3.MP3", _boom)
    assert measure_duration(Path("/tmp/fake.mp3")) is None
