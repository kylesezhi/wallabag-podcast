---
plan name: inline-delete-archive
plan description: AJAX delete/archive without reload
plan status: active
---

## Idea
Currently clicking Delete or Archive on a queue episode reloads the full page, clearing filters. Change these to use fetch() so: delete removes the DOM entry in-place, archive keeps the episode (it stays in podcast), and neither action clears active filters. Backend returns JSON when Accept header indicates AJAX.

## Implementation
- Add a helper `_json_or_redirect` in app/main.py that returns JSONResponse when the request has Accept: application/json, otherwise falls back to _redirect
- Update queue_delete to return JSON {"ok": true} on success or {"error": ...} on failure when Accept: application/json
- Update queue_archive to return JSON {"ok": true} on success or {"error": ...} on failure when Accept: application/json
- In app.js, modify the confirm modal's confirmDelete to use fetch() with Accept: application/json instead of form.submit()
- On delete success, remove the closest .queue-item li from the DOM and re-run the filter apply to update no-match visibility
- On archive success, close the modal and optionally show a brief success flash (no DOM removal since episode stays)
- On fetch failure, show the error message from the JSON response in the modal or as an inline notice
- Run the existing tests to confirm nothing breaks (pytest)

## Required Specs
<!-- SPECS_START -->
- config-and-env
- data-model
- architecture-and-stack
- backend-logging
<!-- SPECS_END -->