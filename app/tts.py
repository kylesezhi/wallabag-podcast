"""Pocket TTS client (async, httpx), MP3 encoding (lameenc), and audio
duration measurement (mutagen).

Talks to a ``pocket-tts serve`` HTTP service (Kyutai Pocket TTS):

- ``POST {base}/tts``    -> synthesized speech streamed as ``audio/wav``
  (multipart form fields: ``text``, optional ``voice_url`` — a built-in
  voice name such as ``"charles"``).
- ``GET  {base}/health`` -> liveness probe (``{"status": "healthy"}``).

No authentication is required. Two quirks of the served WAV shaped this
module:

- The WAV is written to an UNSEEKABLE HTTP stream, so the server cannot
  know the length up front: it emits the RIFF header with PLACEHOLDER
  sizes (``wave.setnframes(1_000_000_000)`` with header patching
  disabled). The declared RIFF/data chunk sizes are therefore bogus and
  are never trusted — a small hand-rolled RIFF parser takes everything
  after the ``data`` chunk header, up to the end of the received bytes,
  as the PCM payload.
- Every response ends with ~200 ms of server-appended trailing silence.
  Durations are measured from the actual bytes, so no trimming is needed
  (it just makes each chunk imperceptibly longer).

The WAV payload is decoded and re-encoded as CBR MP3 with lameenc so the
pipeline keeps writing homogeneous MP3 episodes regardless of the TTS
engine's wire format.
"""

from __future__ import annotations

import functools
import io
import logging
import re
import struct
from pathlib import Path

import httpx
import lameenc
import mutagen.mp3

from .config import Settings, get_settings

logger = logging.getLogger(__name__)

# Pocket TTS synthesis can take a while for long articles; keep a generous
# timeout.
_DEFAULT_TIMEOUT = 120.0

# MP3 encoding parameters shared by speech synthesis and silence generation so
# appended audio stays homogeneous: CBR 64 kbps at Pocket TTS's own sample
# rate (its Mimi codec runs at 24 kHz), lameenc quality 2 (high quality; 7
# would be fastest).
_MP3_BITRATE_KBPS = 64
_MP3_QUALITY = 2
_SILENCE_SAMPLE_RATE = 24000

# ``[pause:Xs]`` tokens are app-side directives produced by the text-cleaning
# pipeline (around the article title and section headings). They must never be
# spoken aloud, so they are stripped defensively before any text leaves this
# client; the generation pipeline owns their real client-side expansion.
_PAUSE_TOKEN_RE = re.compile(r"\[pause:\d+(?:\.\d+)?s\]")

# The English built-in voices shipped with Pocket TTS (verified against the
# bundled english model). Non-English built-ins (giovanni, lola, juergen,
# rafael, daan, estelle, ...) are deliberately excluded — the app speaks
# English articles only.
BUILTIN_VOICES: list[dict[str, str]] = [
    {"id": "alba", "label": "Alba"},
    {"id": "anna", "label": "Anna"},
    {"id": "azelma", "label": "Azelma"},
    {"id": "bill_boerst", "label": "Bill Boerst"},
    {"id": "caro_davy", "label": "Caro Davy"},
    {"id": "charles", "label": "Charles"},
    {"id": "cosette", "label": "Cosette"},
    {"id": "eponine", "label": "Eponine"},
    {"id": "eve", "label": "Eve"},
    {"id": "fantine", "label": "Fantine"},
    {"id": "george", "label": "George"},
    {"id": "jane", "label": "Jane"},
    {"id": "jean", "label": "Jean"},
    {"id": "javert", "label": "Javert"},
    {"id": "marius", "label": "Marius"},
    {"id": "mary", "label": "Mary"},
    {"id": "michael", "label": "Michael"},
    {"id": "paul", "label": "Paul"},
    {"id": "peter_yearsley", "label": "Peter Yearsley"},
    {"id": "stuart_bell", "label": "Stuart Bell"},
    {"id": "vera", "label": "Vera"},
]


class TTSError(Exception):
    """Base class for all TTS client errors."""


class TTSConnectionError(TTSError):
    """Could not reach the TTS server (connect / timeout)."""


def _decode_wav(wav: bytes) -> tuple[int, int, bytes]:
    """Parse a WAV byte buffer into ``(channels, sample_rate, pcm_bytes)``.

    WHY NOT the stdlib ``wave`` module: the Pocket TTS server streams the WAV
    over an unseekable HTTP body, so it writes the RIFF header with
    placeholder sizes (``setnframes(1_000_000_000)`` with header patching
    disabled). The declared RIFF size and ``data`` chunk size are therefore
    bogus — far larger than the actual payload (or even overflowing). This
    parser reads only the ``fmt `` fields it needs and treats EVERYTHING
    after the 8-byte ``data`` chunk header as PCM, ignoring the declared
    size entirely.

    Raises :class:`TTSError` for anything unexpected (non-WAV payload, no
    ``fmt ``/``data`` chunk, non-PCM or non-16-bit encoding, no samples).
    """
    if len(wav) < 12 or wav[0:4] != b"RIFF" or wav[8:12] != b"WAVE":
        raise TTSError("TTS response is not RIFF/WAVE (WAV) audio")

    audio_format: int | None = None
    channels: int | None = None
    sample_rate: int | None = None
    bits_per_sample: int | None = None

    offset = 12  # skip "RIFF" + bogus size + "WAVE"
    while offset + 8 <= len(wav):
        chunk_id = wav[offset:offset + 4]
        # Declared sizes of non-data chunks are trusted (the server writes
        # them correctly as it streams); the data chunk's is not.
        declared = int.from_bytes(wav[offset + 4:offset + 8], "little")
        body_start = offset + 8
        if chunk_id == b"fmt ":
            if declared < 16 or body_start + 16 > len(wav):
                raise TTSError("WAV fmt chunk is truncated")
            (
                audio_format,
                channels,
                sample_rate,
                _byte_rate,
                _block_align,
                bits_per_sample,
            ) = struct.unpack_from("<HHIIHH", wav, body_start)
            offset = body_start + declared + (declared & 1)
        elif chunk_id == b"data":
            # Placeholder sizes again: take every byte to EOF as the payload.
            pcm = wav[body_start:]
            if audio_format != 1:
                raise TTSError(
                    f"WAV is not uncompressed PCM (format {audio_format})"
                )
            if bits_per_sample != 16:
                raise TTSError(
                    f"WAV is not 16-bit PCM ({bits_per_sample} bits per sample)"
                )
            if channels not in (1, 2):
                raise TTSError(f"WAV has an unsupported channel count: {channels}")
            if not sample_rate:
                raise TTSError("WAV has no sample rate")
            # 16-bit frames must be whole: trim a stray trailing byte first,
            # THEN reject an empty payload (a 1-byte mono / 1-3 byte stereo
            # data chunk holds no complete frame at all).
            frame_size = 2 * channels
            pcm = pcm[: len(pcm) - len(pcm) % frame_size]
            if not pcm:
                raise TTSError("WAV contains no audio samples")
            return channels, sample_rate, pcm
        else:
            # Unknown chunk (e.g. LIST): skip via its declared size, keeping
            # the word-alignment padding each chunk carries.
            offset = body_start + declared + (declared & 1)

    raise TTSError("WAV has no data chunk")


def encode_mp3(pcm: bytes, sample_rate: int, channels: int = 1) -> bytes:
    """Encode interleaved little-endian int16 PCM as CBR MP3 via lameenc.

    Uses the module-wide bitrate/quality constants so every MP3 this app
    produces (speech and silence alike) has identical framing parameters.
    """
    encoder = lameenc.Encoder()
    encoder.silence()  # LAME would otherwise chatter directly on C stdout
    encoder.set_bit_rate(_MP3_BITRATE_KBPS)
    encoder.set_in_sample_rate(sample_rate)
    encoder.set_channels(channels)
    encoder.set_quality(_MP3_QUALITY)
    # lameenc returns bytearray slices; normalize to bytes so isinstance
    # checks (e.g. measure_duration_seconds) see real bytes.
    return bytes(encoder.encode(pcm) + encoder.flush())


def wav_to_mp3(wav: bytes) -> bytes:
    """Decode a Pocket TTS WAV response and re-encode it as MP3.

    The server's WAV is mono 16-bit PCM at 24 kHz; the PCM is passed to
    :func:`encode_mp3` at the WAV's own sample rate. When the rate differs
    from :data:`_SILENCE_SAMPLE_RATE` a one-time warning is logged: appended
    silence is always emitted at 24 kHz, so mismatched-rate speech would
    resample in playback — the known degradation (silence still plays, just
    pitched differently).
    """
    channels, sample_rate, pcm = _decode_wav(wav)
    if sample_rate != _SILENCE_SAMPLE_RATE:
        _warn_sample_rate_mismatch(sample_rate)
    return encode_mp3(pcm, sample_rate, channels)


_rate_mismatch_warned = False


def _warn_sample_rate_mismatch(sample_rate: int) -> None:
    """Log the unexpected-sample-rate warning once per process."""
    global _rate_mismatch_warned
    if _rate_mismatch_warned:
        return
    _rate_mismatch_warned = True
    logger.warning(
        "Pocket TTS returned %d Hz audio (expected %d Hz); appended silence "
        "stays at %d Hz and playback resamples the mismatch",
        sample_rate,
        _SILENCE_SAMPLE_RATE,
        _SILENCE_SAMPLE_RATE,
    )


@functools.cache
def encode_silence(seconds: float) -> bytes:
    """Encode ``seconds`` of digital silence as a 24 kHz mono MP3.

    Fractional seconds are allowed and rounded to the nearest sample. The
    generation pipeline requests the same few durations over and over, so
    results are cached (lameenc output is deterministic for identical input).
    Non-positive durations encode nothing and return empty bytes.
    """
    if seconds <= 0:
        return b""
    samples = round(seconds * _SILENCE_SAMPLE_RATE)
    if samples <= 0:
        return b""
    return encode_mp3(b"\x00\x00" * samples, _SILENCE_SAMPLE_RATE, 1)


class TTSClient:
    """Async client for the Pocket TTS (``pocket-tts serve``) HTTP API."""

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
        return self._settings.TTS_BASE_URL.rstrip("/")

    @staticmethod
    def _error_message(resp: httpx.Response) -> str:
        return f"Pocket TTS returned status {resp.status_code}: {resp.text[:200]}"

    # -- public API ---------------------------------------------------------

    async def health(self) -> bool:
        """Return True when the TTS service answers ``GET /health`` with 2xx.

        Never raises for connectivity problems: connection errors, timeouts,
        and non-2xx responses all return False.
        """
        url = f"{self._base_url()}/health"
        client = self._get_or_create_client()
        try:
            resp = await client.get(url)
        except Exception:  # noqa: BLE001  (deliberate: a health probe must never raise)
            # A health probe must never take down its caller (page render):
            # any failure — connect, timeout, malformed URL, closed client —
            # is simply "not healthy".
            return False
        return 200 <= resp.status_code < 300

    async def synthesize(self, text: str, voice: str | None = None) -> bytes:
        """Synthesize speech for ``text`` and return MP3 audio bytes.

        ``[pause:Xs]`` tokens are stripped first (see
        :data:`_PAUSE_TOKEN_RE`). ``voice`` is a built-in voice name; the
        settings default is used when omitted. The server responds with a
        streamed WAV (16-bit mono PCM), which is decoded and re-encoded as
        CBR MP3 (:func:`wav_to_mp3`).

        Raises :class:`TTSConnectionError` on network failure,
        :class:`TTSError` on a non-2xx response or an undecodable WAV, and
        never sends authentication headers (the service has none).
        """
        clean_text = _PAUSE_TOKEN_RE.sub("", text)
        url = f"{self._base_url()}/tts"
        payload_voice = (
            voice if voice is not None else self._settings.TTS_DEFAULT_VOICE
        )
        client = self._get_or_create_client()
        try:
            # Multipart form data matches the server's FastAPI Form fields;
            # ``(None, value)`` tuples render plain data fields.
            resp = await client.post(
                url,
                files={
                    "text": (None, clean_text),
                    "voice_url": (None, payload_voice),
                },
            )
        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            raise TTSConnectionError(
                f"Could not reach Pocket TTS at {url}: {exc}"
            ) from exc

        if not 200 <= resp.status_code < 300:
            raise TTSError(self._error_message(resp))

        return wav_to_mp3(resp.content)


def measure_duration_seconds(audio: Path | bytes) -> float | None:
    """Return the audio duration in (fractional) seconds via mutagen.

    Accepts raw MP3 bytes (a single synthesis chunk) or a path to an MP3 file.
    Returns None when the audio can't be parsed (empty, corrupt, or not
    actually MP3). Never raises, so generation never crashes on bad audio.
    The float precision is used to accumulate exact chapter start times.
    """
    try:
        if isinstance(audio, bytes):
            length = mutagen.mp3.MP3(io.BytesIO(audio)).info.length
        else:
            length = mutagen.mp3.MP3(audio).info.length
        return float(length)
    except Exception:
        return None


def measure_duration(audio: Path | bytes) -> int | None:
    """Return the audio duration in whole seconds via mutagen.

    Accepts raw MP3 bytes (a single synthesis chunk) or a path to an MP3 file.
    Returns None when the audio can't be parsed (empty, corrupt, or not
    actually MP3). Never raises, so generation never crashes on bad audio.
    """
    seconds = measure_duration_seconds(audio)
    return int(seconds) if seconds is not None else None
