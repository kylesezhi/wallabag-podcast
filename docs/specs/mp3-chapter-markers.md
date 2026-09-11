# Spec: mp3-chapter-markers

Scope: feature

# Spec: mp3-chapter-markers

Scope: feature

# MP3 Chapter Markers from Section Titles

Episodes get ID3v2 chapter markers embedded directly in their MP3 so
chapter-aware podcast clients (Apple Podcasts, Overcast, Pocket Casts,
AntennaPod, ...) show headings as chapters. Chapters travel inside the audio
file: no RSS feed changes, no DB schema changes, no new routes.

## When chapters exist

- Chapter detection reuses the section-title mechanism from
  `docs/specs/section-title-pauses.md`: h1-h6 headings plus all-bold
  paragraphs. An episode with ZERO section titles gets no chapters at all.
- When chapters exist, chapter 1 is the episode (article) title at 0:00 —
  the spoken intro. Then one chapter per section title, in document order,
  titled with the raw heading text (not the pronunciation-substituted spoken
  form).
- Only episodes generated after this feature have chapters. Existing done
  episodes are unchanged and chapter-less; delete + re-add the article to
  regenerate it with chapters.

## How timings are computed (exact, not approximate)

- During HTML cleaning each section-title token's leading `[pause:1s]` is
  preceded by a private-use sentinel char (`\ue000`, `_SECTION_MARK` in
  app/textclean.py), and the raw title strings are collected in document
  order (`clean_body_with_sections` / `build_tts_input_with_sections` return
  `(tts_text, section_titles)`; the existing `clean_body` /
  `build_tts_input` / `build_tts_input_from_article` delegate and keep their
  signatures).
- `split_tts_text` forces any sentence CONTAINING the sentinel to begin a
  new chunk, so every section title aligns with a chunk boundary. (The rule
  is "contains", not "starts with": consecutive titles leave the mark
  mid-sentence after the previous title's trailing `[pause:1s]` token.)
- Chapter start = cumulative float-seconds duration of all prior chunks
  (`measure_duration_seconds` in app/kokoro.py, mutagen `info.length`),
  expressed in ms. The sentinel is stripped from chunk text before each
  Kokoro call and must NEVER reach the TTS server.
- Episode total duration = `int(sum of per-chunk float durations + gap)`.
  If any chunk is unparseable (duration `None`) the episode still completes
  with the est-minutes fallback but gets NO chapters (timings unknown).

## Embedding

- `write_id3_chapters(path, chapters, total_ms)` in app/chapters.py writes
  an ordered top-level CTOC frame (`toc`, children `chp0..chpN`) plus one
  CHAP frame per chapter (start ms, end = next start or total_ms, TIT2
  sub-frame carrying the title) via mutagen.id3.
- It runs after the atomic `.part` → `{id}.mp3` rename and before
  `set_episode_done`. The ID3v2 tag sits at the head of the file, so
  byte-range streaming of `/audio/{id}.mp3` is unaffected and the feed
  enclosure length (read at feed-build time) stays accurate.
- Failure isolation: `write_id3_chapters` never raises — on any failure it
  logs a warning and returns False, and the episode is still marked done.
  Chapters are decoration; they can never fail a generation.

## Edge rules

- Consecutive titles: both produce chapters; the second mark may be
  mid-sentence (covered by the contains-rule).
- Multi-sentence titles (e.g. "Section 3. The Part.") span sentences within
  one chunk; the recorded heading string is the display title.
- A title removed by trailing-boilerplate truncation yields no chapter:
  pairing is strictly by mark occurrence, so a missing mark consumes no
  title. An orphan trailing mark left by truncation is dropped after
  `_remove_boilerplate`.
- Duplicate titles (episode title repeated as first heading, etc.) are
  emitted as-is — no de-dup.