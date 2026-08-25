# Spec: episode-end-pause

Scope: feature

# End-of-Episode Pause

## Behavior

- Every generated episode MP3 ends with a configurable silent gap after the final TTS chunk, so back-to-back playback has an audible boundary between articles.
- Gap length defaults to **3 seconds**, controlled by env setting `EPISODE_GAP_SECONDS` (float seconds; fractional values are floored to whole 1-second units; `0` disables appending silence entirely).
- The pause is part of the episode audio itself: it exists in `{id}.mp3`, is included in the episode duration reported to the RSS feed and UI, and survives downloads by podcast clients.

## Constraints

- Silence comes from a static silent-MP3 asset checked into the repo (`app/assets/gap_1s.mp3`, ~1 second), encoded in the same format as Kokoro's mp3 output (matching sample rate, channels, bitrate) so concatenated playback is seamless.
- No new runtime dependencies (no ffmpeg at runtime); the asset ships with the package.
- Silence bytes are appended inside `_synthesize_chunks` **before** the atomic `os.replace(part → final)` rename: a failed or cancelled generation must never leave a partial file with only some of the gap.
- Duration accounting: measured gap duration is added to the per-chunk mutagen sum in `_synthesize_chunks`; if chunk durations were already unparseable (`total = None`), behavior falls back to `est_minutes` as today.
- Only newly generated episodes gain the pause; existing done episodes are unchanged unless regenerated.

## Acceptance Criteria

1. A freshly generated `{id}.mp3` ends with exactly floor(`EPISODE_GAP_SECONDS`) copies of the silence asset appended after the last speech chunk.
2. Episode duration stored via `set_episode_done` includes the appended gap.
3. `EPISODE_GAP_SECONDS=0` produces byte-identical output to the pre-feature pipeline.
4. Cancellation/failure mid-run leaves no `.part` or final file containing a partially-appended gap.
5. Test suite passes (`just test`), including new tests for trailing-silence presence, duration inclusion, and the disabled case.