---
plan name: section-title-pauses
plan description: Deterministic TTS pauses at headings
plan status: active
---

## Idea
Insert Kokoro pause tokens around section titles in articles without any LLM. Wallabag returns reader-mode HTML in ArticleFull.content, where section titles are real heading tags (h1-h6). Today _extract_text (app/textclean.py:123) flattens all structure with soup.get_text(separator=" "), losing headings. The [pause:Xs] token mechanism already works end-to-end: build_tts_input emits tokens that Kokoro-FastAPI interprets natively, split_tts_text never cuts them, and pronunciation substitution cannot corrupt them. Plan: after decomposing removed tags, walk h1-h6 nodes, extract their text, append a period for natural falling prosody, and replace the node with "[pause:1s] {heading}. [pause:1s]" as a plain NavigableString so the existing flatten+clean pipeline carries the tokens through unchanged. Decisions from user: heading tags only (no bold-paragraph heuristic), fixed durations (module constants, matching the hardcoded title/body pauses style), always append terminal period. No new Settings entries; no changes to kokoro.py, pipeline.py, or config.py.

## Implementation
- Add module constants _HEADING_PAUSE_BEFORE/after = '[pause:1s]' (or similar) near _REMOVE_TAGS in app/textclean.py with a comment explaining Kokoro-FastAPI interprets them
- Extend _extract_text in app/textclean.py: after decompose loop, iterate soup.find_all(['h1'..'h6']); skip empty/whitespace-only; build token f'{BEFORE} {text}. {AFTER}' via node.get_text(' ', strip=True) plus appended period; replace heading node with NavigableString(token); then return get_text(separator=' ') as before
- Guard edge cases: empty headings skipped, nested inline markup inside headings flattened by get_text, consecutive headings produce adjacent pause pairs (accepted), hr/br remain removed entirely
- Update app/textclean.py module docstring pipeline description to mention heading pause insertion step
- Add tests in tests/test_textclean.py: h2 gets wrapped tokens with period; heading lacking punctuation gets one; empty heading skipped; <h2><em>markup</em></h2> handled; consecutive headings both wrapped; end-to-end build_tts_input_from_article with realistic article containing headings (extend _article helper usage / test_realistic_wallabag_article pattern)
- Run pytest tests/test_textclean.py plus full suite and lint/typecheck per repo justfile to verify no regressions

## Required Specs
<!-- SPECS_START -->
- config-and-env
- architecture-and-stack
- data-model
- section-title-pauses
<!-- SPECS_END -->