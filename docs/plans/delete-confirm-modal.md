---
plan name: delete-confirm-modal
plan description: Are-you-sure popup before removing episodes
plan status: done
---

## Idea
Replace the existing native window.confirm() dialog (which only guards done-episode deletes) with a styled in-page modal that fires on EVERY Delete press. The current native confirm is in static/js/app.js:43-58 and is gated by data-confirm="true" set only when ep.status == "done" at templates/index.html:78. Staged/failed/generating episodes currently delete with zero confirmation.

Goals:
- All delete presses (staged, failed, generating, done) show a themed modal before the form submits.
- Replace window.confirm() entirely with a custom modal matching the cream/green/tan theme.
- Status-aware copy: done episodes mention mp3 removal (irreversible); others say generic delete + mark-read.

Behavior notes from app/pipeline.py:98-137 and app/main.py:314-339:
- Every delete archives (marks read) the Wallabag article; done also unlinks the mp3 + dedupe row.
- Generating-during-active-run is special: queue_delete cancels the generation task instead of deleting (redirects with "Stopping generation..."). The modal copy ("Delete this episode...") is slightly inaccurate for that one edge case, but it's a rare path and strictly better than today's no-confirmation. Can be refined later if desired.

Implementation approach:
- Template sets a data-confirm-message attribute (status-aware Jinja) on every delete form. JS reads it and populates a single reusable hidden modal. On confirm, JS calls form.submit() (bypasses the submit event so no re-entrant guard needed). Cancel/Escape/overlay-click dismisses.
- No backend changes needed — the POST /queue/{id}/delete route and pipeline are unchanged.

## Implementation
- Add hidden modal markup to the end of the content block in templates/index.html: a .modal-overlay (with backdrop) containing a role=dialog .modal card with a title (#delete-modal-title), body paragraph (#delete-modal-body), and two buttons — Cancel (#delete-modal-cancel, .btn .btn-outline) and Delete (#delete-modal-confirm, .btn .btn-stop). Use the hidden attribute for initial state. This is a single reusable modal populated dynamically by JS.
- Replace the done-only data-confirm="true" conditional on the delete form (templates/index.html:78) with a data-confirm-message attribute on ALL delete forms, using status-aware Jinja copy: done -> 'Delete this episode, remove its audio file, and mark the article as read in Wallabag?'; all other statuses -> 'Delete this episode and mark the article as read in Wallabag?'
- Rewrite the confirm IIFE in static/js/app.js (currently lines 43-58 using window.confirm) into a modal driver: on submit of any form[data-confirm-message], preventDefault + stopImmediatePropagation, populate #delete-modal-body from the attribute, show the modal, store the pending form, and focus #delete-modal-cancel. Confirm button -> hide modal and call pendingForm.submit() (bypasses submit event). Cancel button, Escape key, and overlay click -> hide modal and clear pending form. Restore focus to the triggering button on close.
- Add modal styles to static/css/style.css: .modal-overlay (position:fixed; inset:0; background:rgba(46,42,36,0.4); display:flex; align-items:center; justify-content:center; z-index) and .modal (background:var(--card); border-radius:var(--radius); box-shadow:var(--shadow); max-width; padding; .modal-title, .modal-body, .modal-actions with gap). Reuse existing CSS variables (--card, --danger, --radius, --shadow, button classes).
- Update tests/test_web.py: in test_delete_button_shown_for_staged_and_failed, replace the assertion that data-confirm="true" count == 0 with assertions that staged/failed forms carry data-confirm-message and that the message does NOT mention 'audio file'. In test_delete_button_shown_for_done_with_confirm, assert the done form's data-confirm-message mentions 'audio file'. Optionally add a check that every delete form on the page has data-confirm-message set.
- Run the full test suite (pytest) and any lint/typecheck commands to verify all delete tests pass and no regressions are introduced.

## Required Specs
<!-- SPECS_START -->
- config-and-env
- data-model
- architecture-and-stack
- delete-confirmation-modal
<!-- SPECS_END -->