---
plan name: pronunciation-substitution
plan description: Speak acronyms as words
plan status: done
---

## Idea
Add a configurable pronunciation dictionary so acronyms/abbreviations in article text are spoken as intended words before reaching Kokoro TTS. The user configures a PRONUNCIATIONS env var as comma-separated pairs (e.g. JSON=Jason,API=A.P.I.,SQL=sequel). After the existing text-cleaning pipeline produces clean prose in app/textclean.py, a new substitution step rewrites whole-word, case-insensitive matches to their spoken-form equivalents. This runs on both the cleaned title and cleaned body inside build_tts_input, before the [pause:...] markup assembly, so the text sent to kokoro_client.synthesize() already contains the spoken forms. "Jason" is a real word Kokoro pronounces correctly, so plain substitution is the simplest robust mechanism and works in any language. No Kokoro server-side changes or request-body changes (allow_voice_tags etc.) are needed. The env var follows the existing EXCLUDE_TAGS pattern (NoDecode + BeforeValidator in config.py) so no DB/UI work is required.

## Implementation
- Add a PRONUNCIATIONS field to the Settings class in app/config.py as Annotated[dict[str,str], NoDecode, BeforeValidator(_split_pronunciations)] with default {}. Add a _split_pronunciations parser that turns 'JSON=Jason,API=A.P.I.' into {'JSON':'Jason','API':'A.P.I.'} by splitting on comma then on the first '=' per pair (mirrors the _split_tags helper above it). Clear get_settings cache in tests the same way existing tests do.
- Add an apply_pronunciations(text: str, pronunciations: dict[str,str]) -> str function to app/textclean.py. Build one combined case-insensitive regex \b(?:key1|key2|...)\b (re.IGNORECASE) from the dict keys (escape each key with re.escape), and substitute each match with pronunciations[match.group(0).lower()] (or the original-key lookup). Return text unchanged when the dict is empty. Word boundaries ensure 'JSON' is not replaced inside 'JSONParser'.
- Wire apply_pronunciations into build_tts_input in app/textclean.py: after clean = clean_title(title) and body = clean_body(...), run both through apply_pronunciations(..., get_settings().PRONUNCIATIONS) before the f-string '[pause:0.5s] {clean} [pause:1s] {body}' assembly. This keeps pronunciation as a speech-rendering concern separate from content cleaning, and ensures the [pause:...] tokens themselves are never altered (they contain no whole-word acronym matches).
- Update .env.example with a PRONUNCIATIONS line under the App section (e.g. 'PRONUNCIATIONS=JSON=Jason,API=A.P.I.,SQL=sequel  # comma-separated KEY=SPOKEN pairs; whole-word, case-insensitive') and update docs/specs/config-and-env.md App block to document the new variable and its format.
- Add tests to tests/test_textclean.py covering: (a) apply_pronunciations replaces a known acronym in body text, (b) replacement also applies to the title via build_tts_input, (c) case-insensitive matching ('json' -> 'Jason'), (d) word-boundary safety (no replacement inside a larger word like 'JSONParser'), (e) empty/missing PRONUNCIATIONS is a no-op (existing tests still pass), (f) surrounding punctuation preserved ('JSON,' -> 'Jason,'), and (g) the [pause:...] tokens in build_tts_input output are untouched. Use the existing _set_required_env + get_settings.cache_clear pattern for settings-dependent tests.
- Add or extend a test in tests/test_pipeline.py that sets PRONUNCIATIONS (via monkeypatch.setenv + get_settings.cache_clear) and asserts the synthesized text reaching the mock Kokoro handler contains the spoken form (e.g. 'Jason') and not the original acronym ('JSON'), reusing the existing kokoro_calls request-capture pattern.

## Required Specs
<!-- SPECS_START -->
- config-and-env
- data-model
- architecture-and-stack
<!-- SPECS_END -->