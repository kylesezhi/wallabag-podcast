---
plan name: chunk-progress-wording
plan description: Clearer grey chunk counters
plan status: done
---

## Idea
The generating episode row shows a terse "4/36" right after the "generating" status pill, which is unclear about what it counts. Change all chunk-progress strings to read like "0 of 36 chunks completed", keep them in the muted grey panel text style, and keep the server-rendered template, the JS poller (static/js/app.js rebuilds these strings on every 2s /queue/status poll), and tests consistent. Spots affected: (1) queue-row badge span#ep-progress-{id} in templates/index.html:83, (2) progress-card line p#progress-chunk in templates/index.html:43-47, (3) app.js poll() label building (~lines 42-58), (4) tests/test_web.py:962 assertion ("4/12 chunks synthesized"). CSS .queue-item-progress and .progress-chunk already use var(--text-muted) so likely no color change needed; verify sizing matches adjacent muted meta text.

## Implementation
- templates/index.html:83 — render badge as "{{ ep.progress_done }} of {{ ep.progress_total }} chunks completed" instead of "done/total"
- templates/index.html:43-47 — change progress-card line to "{{ current_gen.progress_done }} of {{ current_gen.progress_total }} chunks completed" for consistent wording
- static/js/app.js — update poll() to build "X of Y chunks completed" for both ep-progress badge and #progress-chunk line (replace "/" join and "chunks synthesized" suffix)
- static/css/style.css — confirm .queue-item-progress / .progress-chunk match muted panel text (var(--text-muted)); only tweak font-size/spacing if visually off vs .queue-item-meta
- tests/test_web.py:962 — update expected string to "4 of 12 chunks completed"
- Run pytest (and any lint) to verify nothing else asserts the old "a/b" or "chunks synthesized" formats

## Required Specs
<!-- SPECS_START -->
- architecture-and-stack
- data-model
- config-and-env
<!-- SPECS_END -->