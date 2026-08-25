---
plan name: bold-paragraph-pauses
plan description: Bold paragraphs become section titles
plan status: done
---

## Idea
Extend the existing heading preprocessing in app/textclean.py so that bold-only paragraphs are also treated as section titles. Wallabag reader-mode HTML frequently marks section titles as <p><strong>Title</strong></p> (or <b>) rather than real heading tags, and today those get flattened into ordinary prose by _extract_text. User decisions (reversing the original 'no bold-paragraph heuristic' choice from the section-title-pauses plan): match <strong> AND <b>; a paragraph qualifies only when EVERY non-whitespace character descends from a strong/b element (so <p>Some <strong>bold</strong> prose</p> stays prose); qualifying paragraphs receive identical treatment to h1-h6 — replaced by "[pause:1s] Text. [pause:1s]" using the same _HEADING_PAUSE_BEFORE/AFTER constants and _ensure_terminal_punctuation. No changes outside app/textclean.py plus its tests; no Settings entries; kokoro.py/pipeline.py/config.py untouched. Verification is `just test` (repo has no lint/typecheck recipes).

## Implementation
- Add constant _BOLD_PARAGRAPH_TAGS = ("strong", "b") near _HEADINGS in app/textclean.py with a comment explaining Wallabag articles use bold-only paragraphs as pseudo-headings
- Add helper _all_text_is_bold(node) -> bool in app/textclean.py: walk node.descendants; return False if any non-whitespace NavigableString lacks an ancestor whose name is in _BOLD_PARAGRAPH_TAGS
- Extend _wrap_headings_with_pauses (rename to _wrap_section_titles_with_pauses for accuracy, updating its one call site in _extract_text): after the heading loop, iterate soup.find_all("p"); skip empty/whitespace-only and not-fully-bold paragraphs; replace each qualifying paragraph with NavigableString(f"{_HEADING_PAUSE_BEFORE} {_ensure_terminal_punctuation(text)} {_HEADING_PAUSE_AFTER}") exactly like headings
- Update the module docstring step 3 in app/textclean.py to mention that bold-only paragraphs (<p><strong>/<b>) are treated as section titles
- Add tests to section 9b of tests/test_textclean.py: <p><strong>Title</strong></p> gets tokens+period; <p><b>Title</b></p> works; nested <p><strong><em>Nested</em></strong></p> flattens; partially-bold paragraphs (<p><strong>Lead</strong> more prose.</p>) stay unwrapped; whitespace-only <p><strong>   </strong></p> skipped; end-to-end build_tts_input_from_article mixing an h2 and a bold paragraph
- Run `just test` (full pytest suite) and confirm no regressions

## Required Specs
<!-- SPECS_START -->
- config-and-env
- data-model
- architecture-and-stack
- bold-para-pauses
- section-title-pauses
<!-- SPECS_END -->