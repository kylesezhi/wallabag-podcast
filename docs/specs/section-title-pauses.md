# Spec: section-title-pauses

Scope: feature

# Feature: Section Title Pauses

Insert deterministic Kokoro pause tokens around section titles in article bodies — no LLM, regex + BeautifulSoup only.

## Background

Wallabag returns reader-mode **HTML** in `ArticleFull.content` (app/wallabag.py). Section titles are real heading tags (`h1`–`h6`). Today `_extract_text` flattens all structure with `soup.get_text(separator=" ")`, so headings become indistinguishable from prose.

The `[pause:Xs]` token mechanism already works end-to-end:

- `build_tts_input` emits tokens that the Kokoro-FastAPI server interprets natively.
- `split_tts_text` never splits inside a token.
- Pronunciation substitution cannot corrupt tokens.

## Behavior

In `_extract_text` (app/textclean.py), after the `_REMOVE_TAGS` decompose loop and before flattening:

1. Find all `h1`–`h6` elements.
2. Skip empty / whitespace-only headings.
3. Extract heading text with `get_text(" ", strip=True)` (flattens nested inline markup like `<em>`).
4. Append a terminal period for natural falling TTS prosody.
5. Replace the node with `NavigableString("[pause:1s] {heading}. [pause:1s]")`.

The flattened output then flows through the existing pipeline unchanged (`unescape`, URL/email removal, boilerplate truncation, whitespace normalization, punctuation fix).

## Decisions

- **Detection:** heading tags only; no bold-paragraph heuristic.
- **Durations:** fixed module constants (`[pause:1s]` before/after), same style as the hardcoded title/body pauses in `build_tts_input`. No new `Settings`.
- **Punctuation:** always append a period to heading text.
- Consecutive headings produce adjacent pause pairs — accepted, Kokoro handles repeated tokens.
- `min_chars` threshold counts marker characters (~22 per heading) — accepted as negligible.
- No changes to `kokoro.py`, `pipeline.py`, or `config.py`.

## Tests

In tests/test_textclean.py:

- h2 gets wrapped tokens with appended period
- heading lacking punctuation gets exactly one period
- empty heading skipped
- `<h2><em>markup</em></h2>` handled
- consecutive headings both wrapped
- end-to-end `build_tts_input_from_article` with a realistic article containing headings

## Verification

Run full pytest suite plus repo lint/typecheck (see justfile).