---
plan name: episode-end-pause
plan description: Append silent gap audio
plan status: done
---

## Idea
Episodes currently end the instant the last TTS chunk finishes, so back-to-back playback runs one article straight into the next with no boundary. Add a configurable end-of-episode pause (default 3 seconds) by appending silent MP3 bytes to each episode file during generation.

Design decisions (confirmed with user): pause length default 3s, exposed as env setting `EPISODE_GAP_SECONDS` so it can be tuned without code changes; silence sourced from a static silent-MP3 asset checked into the repo (matching Kokoro's mp3 output format) — no ffmpeg or other runtime dependency.

Implementation surface is small and localized:
- `_synthesize_chunks` (app/pipeline.py:217-251) already streams chunks into `{id}.mp3.part` then atomically renames. After the final chunk write, append the gap silence before the rename, and include its measured duration in the returned total so RSS/UI durations stay accurate.
- Settings lives in app/config.py (pydantic-settings); docs/specs/config-and-env.md and .env.example document env vars and must be updated.
- Only newly generated episodes get the pause; existing done episodes are unchanged unless regenerated.

Note: user chose "default to 3"; implemented per repo convention as an overridable env setting defaulting to 3.0 rather than a hardcoded constant.

## Implementation
- Probe a real Kokoro /v1/audio/speech mp3 response with mutagen to record its sample rate, channels, and bitrate; generate a 1-second silent MP3 in exactly that format (e.g. via ffmpeg locally once) and commit it as app/assets/gap_1s.mp3.
- Add EPISODE_GAP_SECONDS: float = 3.0 to Settings in app/config.py with an explanatory comment, and document it in .env.example and docs/specs/config-and-env.md.
- Make the asset ship with the package: add package-data config for app/assets/*.mp3 in pyproject.toml ([tool.setuptools.package-data]).
- In app/pipeline.py add a module-level cached _gap_silence() loader (importlib.resources) returning the asset bytes plus its mutagen-measured duration; in _synthesize_chunks append int(settings-free copies based on EPISODE_GAP_SECONDS passed in) after the last chunk write, before os.replace, and add the gap duration to the returned total.
- Update tests: adjust any tests asserting exact final-file composition to stub/patch the gap loader; add new tests covering (a) final mp3 ends with the silence bytes, (b) reported duration includes the gap, (c) EPISODE_GAP_SECONDS=0 produces no appended silence.
- Verify: run 'just test'; manually regenerate one episode against real Kokoro and confirm the trailing pause is audible, durations in UI/feed reflect the extra 3s.

## Required Specs
<!-- SPECS_START -->
- config-and-env
- architecture-and-stack
- data-model
- episode-end-pause
<!-- SPECS_END -->