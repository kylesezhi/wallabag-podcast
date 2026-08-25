---
plan name: clear-staged-confirm
plan description: Confirm before emptying queue
plan status: done
---

## Idea
Wire the existing reusable "Are you sure?" confirmation modal up to the Clear Staged button so an accidental click cannot wipe staged/failed episodes. The modal driver in static/js/app.js already intercepts every submit of any form carrying data-confirm-message (document-level listener, app.js:162-172) and reuses the single #delete-modal markup at templates/index.html:134-143 — so the core change is adding data-confirm-message to the POST /queue/clear form at templates/index.html:51-53. Backend is untouched: POST /queue/clear (app/main.py:382-385) calls pipeline.clear_queue() which removes staged AND failed episodes while keeping done ones (verified by test_clear_queue, tests/test_web.py:590). Per user decisions: (1) add dynamic confirm-button label support via an optional data-confirm-label attribute so this modal reads "Clear" instead of the hard-coded "Delete"; (2) message copy explicitly mentions that failed episodes go too and done episodes are kept. Frontend-only change across templates/index.html + static/js/app.js plus one template assertion test; no route/pipeline/CSS changes needed since modal styles already exist.

## Implementation
- templates/index.html (lines 51-53): add two attributes to the POST /queue/clear form — data-confirm-message="Remove all staged and failed episodes from the queue? Done episodes are kept." and data-confirm-label="Clear". Do not touch any other form or the modal markup.
- static/js/app.js (modal driver IIFE, lines 115-193): read an optional per-form label — in open(), set confirmBtn.textContent = form.getAttribute("data-confirm-label") || "Delete" (pass the form into open(); it already receives it); in close() or before each subsequent open(), restore the default "Delete" so episode-delete modals keep their current label.
- tests/test_web.py (near the existing confirm assertions at lines 809-880): add a test that GETs "/" with a staged+failed fixture and asserts the rendered page contains the clear form's data-confirm-message text and data-confirm-label="Clear", alongside the existing per-episode data-confirm-message checks.
- Run the full pytest suite (pytest) and confirm all existing delete/confirm/clear tests still pass.
- Optional manual sanity check: serve the app, click Clear Staged with mixed-status rows — modal opens with body copy and a 'Clear' confirm button; Escape/backdrop-click/Cancel dismiss without posting; confirming posts /queue/clear and redirects with 'Cleared N episodes'. Episode Delete modals still show 'Delete' as the confirm label.

## Required Specs
<!-- SPECS_START -->
- config-and-env
- data-model
- architecture-and-stack
- delete-confirmation-modal
<!-- SPECS_END -->