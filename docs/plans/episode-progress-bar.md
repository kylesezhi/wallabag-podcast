---
plan name: episode-progress-bar
plan description: Visual chunk fill indicator
plan status: done
---

## Idea
Replace the grey "4 of 12 chunks synthesized" text next to the GENERATING pill with a small custom-styled progress bar (track div + green fill div, theme vars --cream-dark / --green, ~7rem wide, rounded, width % = progress_done/progress_total). Bar-only visual at episode level; hover title and aria-label carry "N of M chunks synthesized"; role="progressbar" with aria-valuemin/max/now kept updated by the JS poller. Remove the chunks text line from the progress card entirely (delete p#progress-chunk above Stop Generating plus its now-unused {% set current_gen %}), its CSS rule, and the poller's chunkEl branch. Update test_web.py assertions accordingly (bar markup + aria values rendered server-side; progress-chunk absent).

## Implementation
- templates/index.html — swap span#ep-progress-{id} content to track+fill markup (<span class="queue-item-progress" role="progressbar" aria-label="chunks synthesized" aria-valuemin/max/now + title="N of M chunks synthesized"> with inner .queue-item-progress-fill width {{ ((done or 0)/total*100)|round(1) }}%, guarded by progress_total); delete p#progress-chunk and the unused {% set current_gen %}
- static/css/style.css — rework .queue-item-progress into an inline-block rounded track (~7rem x 0.5rem, background var(--cream-dark), overflow hidden); add .queue-item-progress-fill { height:100%; background: var(--green); transition: width } ; remove obsolete .progress-chunk rule
- static/js/app.js — remove chunkEl lookup/branch; poll() updates #ep-progress-{genEp.id}: set fill style.width %, aria-valuenow, title "X of Y chunks synthesized", unhide when progress_total present (parity with old show/hide)
- tests/test_web.py — update test_home_renders_generating_row_with_progress: assert ep-progress-1 renders with aria-valuenow="4"/aria-valuemax="12" and a fill width style; assert 'id="progress-chunk"' not in response.text; drop old string assertions
- Run .venv/bin/python -m pytest -q; visually sanity-check the bar in the generating row

## Required Specs
<!-- SPECS_START -->
- architecture-and-stack
- data-model
- config-and-env
<!-- SPECS_END -->