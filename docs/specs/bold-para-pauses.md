# Spec: bold-para-pauses

Scope: feature

# Feature: Bold Paragraph Pauses

Extends the section-title pause preprocessing (feature `section-title-pauses`) so bold-only paragraphs are treated as section titles. **Supersedes** that spec's decision "heading tags only; no bold-paragraph heuristic."

## Background

Wallabag reader-mode HTML frequently encodes section titles as `<p><strong>Title</strong></p>` (or `<b>`) instead of real heading tags. `_extract_text` (app/textclean.py) flattens these into ordinary prose, indistinguishable from body text.

## Detection (app/textclean.py)

After the h1–h6 wrapping in the heading-preprocessing step:

1. Iterate all remaining `p` elements.
2. Skip empty / whitespace-only paragraphs.
3. Qualify only when **every non-whitespace character descends from a `<strong>` or `<b>` element** (`_BOLD_PARAGRAPH_TAGS = ("strong", "b")`). Partially-bold prose (`<p><strong>Lead</strong> more prose.</p>`) stays unwrapped.
4. Replace the paragraph with `NavigableString("[pause:1s] {text}. [pause:1s]")` — identical tokens, durations, and `_ensure_terminal_punctuation` treatment as headings.

## Decisions

- Tags: `<strong>` and `<b>`. No `<em>`/italic handling.
- Match rule: all visible text must come from strong/b descendants (nested markup like `<strong><em>T</em></strong>` qualifies via flattened `get_text`).
- Same `[pause:1s]` before/after constants as headings; always append terminal period.
- Empty/whitespace-only bold paragraphs are skipped.
- Bold paragraphs inside removed containers (nav/footer/table/…) never reach this step.
- No new Settings entries; no changes to kokoro.py, pipeline.py, or config.py.

## Tests (tests/test_textclean.py, section 9b)

- `<p><strong>Title</strong></p>` → wrapped tokens with appended period
- `<p><b>Title</b></p>` → wrapped
- `<p><strong><em>Nested</em></strong></p>` → flattened and wrapped
- Partially-bold paragraphs stay plain prose
- Whitespace-only bold paragraph skipped
- End-to-end `build_tts_input_from_article` mixing an h2 and a bold paragraph

## Verification

`just test` (full pytest suite). Repo has no lint/typecheck recipes.