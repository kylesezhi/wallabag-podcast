---
plan name: mp3-chapter-markers
plan description: ID3 timestamps at section titles
plan status: done
---

## Idea
Embed ID3v2 chapter markers (CTOC + CHAP frames) in each episode's MP3 so podcast clients show chapter headings at section titles. Section titles are already detected during cleaning (app/textclean.py:161 `_wrap_section_titles_with_pauses` wraps h1-h6 + all-bold paragraphs in `[pause:1s] Title. [pause:1s]` tokens) and per-chunk durations are already measured during synthesis (app/pipeline.py:452 `measure_duration`) — so exact chapter start times are computable at generation time with no re-architecture.

User decisions: (1) ID3 tags embedded in the MP3 only — no RSS changes, no DB schema changes, no new routes; (2) chapter 1 = episode title at 0:00 ("intro" chapter), then one chapter per section title; (3) chapters only for episodes generated after this change — existing done episodes stay chapter-less (regenerate via delete + re-add); episodes with zero section titles get no chapters at all.

Mechanism:
- A private-use sentinel char (`\ue000`, module constant in textclean) is prepended to each section-title token's leading `[pause:1s]` during the HTML walk, and the title strings are collected in document order. New API `clean_body_with_sections` / `build_tts_input_with_sections` return `(tts_text, section_titles)`; existing `clean_body` / `build_tts_input` / `build_tts_input_from_article` delegate and keep their signatures and tests intact. The sentinel records WHERE each title starts in the flattened text (a regex over flattened text is unreliable: the intro's trailing `[pause:1s]` + first heading's leading token would false-match body text as a title; and `find()` on recorded titles breaks on entity-unescape/URL-removal/pronunciation rewrites).
- `split_tts_text` gains one rule: a sentence CONTAINING the sentinel always begins a new chunk (consecutive titles leave the mark mid-sentence after the previous title's trailing token, so "starts with" is insufficient). This aligns every section title to a chunk boundary, making chapter start = cumulative duration of all prior chunks — exact, no approximation. Sentinels are never cut and never alter the joining-reconstruction property.
- New `measure_duration_seconds` in app/kokoro.py returns float seconds (mutagen `info.length`); `measure_duration` stays an int wrapper so its contract/tests are unchanged. Pipeline switches to the float variant: chapter starts in ms come from the running float sum before each marked chunk, and the episode total becomes `int(sum(floats) + gap)` — strictly more accurate than today's sum of per-chunk `int()` truncations.
- New app/chapters.py `write_id3_chapters(mp3_path, chapters, total_ms)`: mutagen ID3 CTOC (element_id "toc", child ids chp0..chpN, ordered top-level) + one CHAP per chapter (start ms, end = next start or total_ms, TIT2 sub-frame with the title). Never raises — on any failure log a warning and return False so chapter embedding can never fail an episode (same spirit as `measure_duration`).
- Pipeline `_synthesize_chunks`: strips the sentinel from each chunk's text before the Kokoro call (the mark must never reach the TTS server), accumulates per-chunk float durations, pairs sentinel-containing chunks with the recorded titles in order (zip-by-mark-occurrence so a title removed by trailing-boilerplate cut simply yields no chapter), prepends `(0, episode_row_title)` when ≥1 section chapter exists, and after the atomic `os.replace` calls `write_id3_chapters` — skipped when there are no sections or the duration is unparseable (None).

Edge cases to handle: consecutive titles (mark mid-sentence — covered by the contains-rule); multi-sentence titles like "Section 3. The Part." span sentences but the recorded HTML-walk string is the display title; a trailing orphan mark left by boilerplate truncation is dropped (guard after `_remove_boilerplate`); duplicate episode/section titles are emitted as-is (no de-dup); ID3 write failure is warning-only.

Testing: no real TTS needed — existing fakes plus the packaged `app/assets/gap_1s.mp3` as a real MP3 fixture for round-tripping mutagen chapter frames. Update ~14 `monkeypatch.setattr("app.pipeline.measure_duration", ...)` targets in tests/test_pipeline.py to the new float function. Run `uv run pytest -q` and `uv run ruff check`.

## Implementation
- [object Object]
- [object Object]
- [object Object]
- [object Object]
- [object Object]
- [object Object]
- [object Object]
- [object Object]

## Required Specs
<!-- SPECS_START -->
- config-and-env
- data-model
- architecture-and-stack
- backend-logging
- mp3-chapter-markers
<!-- SPECS_END -->