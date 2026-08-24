---
plan name: generating-spinner-disable
plan description: Busy state on drive card
plan status: done
---

## Idea
While a TTS generation run is active, give clear busy feedback in the UI. Today the Generate Audio button (templates/index.html:24) stays clickable during a run — clicking it only produces an error flash from the server-side guard in queue_generate (app/main.py:343-357). And nothing visually spins; the progress card shows static "Generating… X of Y" text.

Scope decided with user:
- Spinner goes ON the Generate Audio button itself (not the progress card).
- Only Generate Audio is disabled; Add Random Articles / Clear Staged stay untouched.
- Progress card keeps its existing counts + Stop button as-is.

Implementation notes:
- The template already has the `generating` flag available, so this is server-rendered: `{% if generating %}disabled{% endif %}` on the button, label swapped to "Generating…" with an inline spinner span while busy.
- Pure-CSS spinner (border-circle + @keyframes spin, currentColor) added to static/css/style.css — no new dependencies, matches existing hand-rolled CSS approach.
- No JS changes needed: static/js/app.js polls /queue/status and reloads the page when the run finishes, which automatically restores the enabled button. Stop Generating (/queue/stop) also re-renders with generating=false.
- Tests: tests/test_web.py::test_home_progress_counts_generating_episode already sets app.state.generating = True — extend it (or add siblings) to assert the disabled attribute + spinner markup appear when generating and are absent otherwise.

Acceptance:
- Mid-run, Generate Audio renders disabled with a spinning indicator and non-clickable.
- When idle, button renders exactly as before (enabled, "Generate Audio").
- Run finishing or being stopped re-enables the button (via existing reload/redirect behavior).
- All existing tests keep passing; new assertions cover both states.

## Implementation
- Add .spinner CSS to static/css/style.css: small inline pure-CSS rotating circle (border-based, currentColor, ~1em) with a @keyframes spin rule, suitable inside a button label.
- Add .btn:disabled styling to static/css/style.css: reduced opacity, cursor: not-allowed, and suppress the .btn-primary:hover background change so a disabled primary button doesn't react to hover.
- Update templates/index.html Generate Audio button: when {{ generating }} is true render it with the disabled attribute, aria-busy="true", label 'Generating…' plus <span class="spinner" aria-hidden="true"></span>; keep the current enabled 'Generate Audio' markup otherwise.
- Extend tests/test_web.py: in/next to test_home_progress_counts_generating_episode assert the button carries disabled + spinner markup while app.state.generating = True, and add/extend an idle-state home test asserting the plain enabled 'Generate Audio' button.
- Run the test suite (per justfile, e.g. just test / pytest) and fix any fallout until green.
- Manual smoke check with the dev server: start a run (button shows spinner, disabled, unclickable), click Stop Generating (button re-enables), and reload mid-run to confirm the disabled state persists.

## Required Specs
<!-- SPECS_START -->
- architecture-and-stack
- config-and-env
- data-model
<!-- SPECS_END -->