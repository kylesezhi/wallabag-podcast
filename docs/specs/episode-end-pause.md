# Spec: episode-end-pause

Scope: feature

# End-of-Episode Pause

## Behavior

- Every generated episode MP3 ends with a configurable silent gap after the final TTS chunk, so back-to-back playback has an audible boundary between articles.
- Gap length defaults to **3 seconds**, controlled by env setting `EPISODE_GAP_SECONDS` (float seconds; fractional values are floored to whole 1-second units; `0` disables appending silence entirely).
- The pause is part of the episode audio itself: it exists in `{id}.mp3`, is included in the episode duration reported to the RSS feed and UI, and survives downloads by podcast clients.

## Constraints

- Silence is generated in-process by the **same lameenc MP3 encoder as the speech** (`encode_silence` in app/tts.py, 64 kbps CBR, 24 kHz mono — homogeneous framing with synthesized audio, so concatenated playback is seamless). Results are cached per duration; one-second units are reused across chunks and for the gap.
- No asset packaging or new runtime dependencies (no ffmpeg at runtime); the silence lives in code.
- Silence bytes are appended inside `_synthesize_chunks` **before** the atomic `os.replace(part → final)` rename: a failed or cancelled generation must never leave a partial file with only some of the gap. The writes contain no awaits, so a cancellation cannot interrupt mid-gap.
- Duration accounting: the measured gap duration is added to the measured silence/chunk sum in `_synthesize_chunks`; if that sum was already unparseable (`total = None`), behavior falls back to `est_minutes` as today.
- Only newly generated episodes gain the pause; existing done episodes are unchanged unless regenerated.

## Acceptance Criteria

1. A freshly generated `{id}.mp3` ends with exactly floor(`EPISODE_GAP_SECONDS`) one-second silences appended after the last chunk (0 copies when floored ≤ 0, including negative/unclamped values).
2. Episode duration stored via `set_episode_done` includes the appended gap (measured, multiplied by the copy count).
3. `EPISODE_GAP_SECONDS=0` produces the same output as a gap-free run (no gap bytes ever fetched).
4. Cancellation/failure mid-run leaves no `.part` or final file containing a partially-appended gap.
5. Test suite passes (`uv run pytest -q` / `mise run test`), including tests for trailing-silence presence, duration inclusion, and the disabled case.