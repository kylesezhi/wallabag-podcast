---
plan name: chunked-audio-generation
plan description: Stream TTS chunks to disk
plan status: done
---

## Idea
Problem: audio generation fails on RAM exhaustion. Today generate_all() sends each episode's ENTIRE TTS text in one Kokoro POST and buffers the whole MP3 response in memory before writing to disk (app/pipeline.py:249-252, app/kokoro.py synthesize, app/main.py _run_generation). Long articles blow up both the Kokoro server (huge input synthesized at once) and the app process (all audio bytes held in RAM). TODO.md already lists "chunk the audio so longer files are possible".

Fix: client-side chunked generation with incremental disk writes and granular progress.

1) Chunking (app/textclean.py): new sentence-boundary splitter split_tts_text(text, max_chars): accumulate sentences (. ! ? boundaries), hard-split oversized sentences at word boundaries; never split inside [pause:...] tokens (they contain no sentence punctuation so they survive). New Settings.KOKORO_MAX_CHUNK_CHARS env setting (config.py + .env.example), default 2000. The existing build_tts_input string ([pause:0.5s] title [pause:1s] body) is chunked as-is.

2) Streamed loop (pipeline.generate_all per episode): split into N chunks; open DATA_DIR/audio/{id}.mp3.part ("wb"); per chunk: write DB progress -> await synthesize(chunk, voice) with ONE automatic retry on KokoroError before failing -> measure chunk duration via mutagen over BytesIO (extend measure_duration to accept bytes|Path) -> append bytes to .part file -> update DB progress. After last chunk atomically os.replace .part -> {id}.mp3; duration = sum of per-chunk durations, falling back to est_minutes when any measurement is None (same fallback contract as today).

3) Semantics preserved: any failure/cancel best-effort unlinks the stale .part; CancelledError still marks in-flight episode failed ("Cancelled by user") and halts the run; SkipArticle/KokoroError/WallabagError isolation unchanged; mid-run staged-delete skip check unchanged; reset_failed_to_staged clears progress columns.

4) Progress persistence + UI: episodes table gains progress_done/progress_total INTEGER columns via guarded ALTER TABLE migration in init_db (existing DBs upgraded); db.py adds set_episode_progress() and includes progress fields in get_queue_episodes(); /queue/status returns them automatically. templates/index.html: generating row badge shows "generating 4/12"; progress card shows current chunk line. static/js/app.js poller updates both from data.episodes during its existing 2s poll.

5) Tests: textclean splitter unit tests; pipeline tests (multi-chunk POSTs recorded by mock transport, byte-append produces concatenated file, retry-once behavior, .part cleanup on failure/cancel, duration summation, progress columns updated); web test for status JSON fields; init_db migration test on legacy schema.

6) Docs: update docs/specs/architecture-and-stack.md convention (replace "one Kokoro call per article" with client-side chunking), docs/specs/data-model.md (new columns + generate flow), docs/specs/config-and-env.md (new env var); remove TODO.md chunk line.

## Implementation
- Add KOKORO_MAX_CHUNK_CHARS (default 2000) to Settings in app/config.py plus .env.example, and document it in docs/specs/config-and-env.md.
- Implement split_tts_text(text, max_chars) sentence-boundary splitter in app/textclean.py that never splits inside [pause:...] tokens and hard-splits overlong sentences at word boundaries.
- Extend app/db.py: guarded ALTER TABLE migration adding progress_done/progress_total to episodes in init_db; add set_episode_progress(conn, id, done, total); include both fields in get_queue_episodes(); clear them in reset_failed_to_staged().
- Rework the episode loop in pipeline.generate_all(): iterate chunks from split_tts_text, synthesize each chunk with one retry on KokoroError, append bytes to {id}.mp3.part, sum per-chunk durations via extended measure_duration(bytes|Path), persist progress after each chunk, atomic rename at end, unlink .part on failure/cancel.
- Surface chunk progress in UI: index.html badge + progress card markup for the generating row, extend static/js/app.js poll() to render progress_done/progress_total from /queue/status episodes.
- Extend measure_duration in app/kokoro.py to accept bytes via mutagen over BytesIO while keeping Path support.
- Add tests: splitter edge cases (tests/test_textclean.py), multi-chunk synthesis/retry/cleanup/duration/progress (tests/test_pipeline.py), status JSON + legacy-schema migration (tests/test_web.py).
- Run just test until green; update docs/specs/architecture-and-stack.md + data-model.md conventions and drop the TODO.md chunk line.

## Required Specs
<!-- SPECS_START -->
- config-and-env
- data-model
- architecture-and-stack
- chunked-audio-generation
<!-- SPECS_END -->