---
plan name: speak-article-date
plan description: Speak Wallabag date after title
plan status: active
---

## Idea
Spoken episode intro gains the article's date after the title: "[pause:0.5s] {title} [pause:0.5s] July 4, 2024 [pause:1s] {body}". Date selection: prefer the Wallabag entry's published_at, fall back to created_at when published_at is null/blank/unparseable; if neither yields a usable date, the intro stays exactly as today (never fails generation).

Research findings (production path): main.podcast_generate -> pipeline.generate_all (app/pipeline.py:512) fetches the entry via WallabagClient.get_entry -> ArticleFull, then calls build_tts_input_from_article_with_sections -> build_tts_input_with_sections (app/textclean.py:483-503) which assembles f"[pause:0.5s] {clean} [pause:1s] {body}". The Wallabag API payload already includes created_at (and published_at) but _parse_meta/_parse_full (app/wallabag.py:333-360) drop them; ArticleFull/ArticleMeta have no date fields. Python >= 3.11 so datetime.fromisoformat handles +0000 and Z offsets and date-only strings.

## Implementation
- app/wallabag.py: add defaulted fields published_at: str | None = None and created_at: str | None = None to ArticleMeta and ArticleFull (defaults keep all existing fixtures constructing them working); capture both in _parse_meta via item.get('published_at')/item.get('created_at') and pass through in _parse_full
- app/textclean.py: add a defensive helper (e.g. _spoken_date(published_at, created_at) -> str | None) that picks published_at if parseable and plausible (year > 1970; blank/null/unparseable rejected), else created_at under the same rule, else None; parse with datetime.fromisoformat/date.fromisoformat and format as 'July 4, 2024' via f'{dt:%B} {dt.day}, {dt.year}' (no UTC conversion - keep Wallabag's own offset so it matches the Wallabag UI)
- app/textclean.py: extend build_tts_input_with_sections and build_tts_input with an optional spoken_date: str | None = None param; when present assemble '[pause:0.5s] {title} [pause:0.5s] {spoken_date} [pause:1s] {body}' (spoken_date passes through apply_pronunciations like title/body); when None keep the exact current output
- app/textclean.py: build_tts_input_from_article_with_sections and build_tts_input_from_article compute the date from article.published_at/article.created_at via the new helper and pass it down; app/pipeline.py needs no change (it calls the article wrapper)
- tests/test_wallabag.py: _meta_item gains published_at; assert _parse_meta/_parse_full and get_entry capture both dates
- tests/test_textclean.py: extend _article fixture with published_at/created_at params (default None so existing tests are untouched); new tests for: published date spoken after title, fallback to created_at, both missing -> byte-identical current output, blank/unparseable rejected, date-only string accepted, 1970 junk rejected, pronunciation pass leaves pause tokens intact
- tests/test_pipeline.py: leave _entry_payload unchanged (no dates -> existing exact-text assertions stay green); add one focused test using a handler payload with published_at asserting the first chunk sent to the fake Kokoro client contains the spoken date
- Run the full suite (python3 -m pytest tests/) and confirm green; no DB, RSS, UI, or chapter-marker changes - the date is spoken only (intro_title chapters keep title-only)

## Required Specs
<!-- SPECS_START -->
- config-and-env
- data-model
- architecture-and-stack
- backend-logging
<!-- SPECS_END -->