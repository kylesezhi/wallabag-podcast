# Spec: delete-confirmation-modal

Scope: feature

# Delete Confirmation Modal — Feature Spec

## Purpose
Every episode Delete press must show a themed in-page confirmation modal before the
delete form submits. This replaces the previous native `window.confirm()` dialog (which
only guarded **done** episodes) and extends confirmation to **all** statuses so no
episode is removed without an explicit second action.

## Scope
- Frontend only: `templates/index.html`, `static/js/app.js`, `static/css/style.css`.
- No backend changes — the `POST /queue/{id}/delete` route and `app/pipeline.delete_item`
  are untouched. The modal is purely a client-side gate in front of the existing form submit.

## Behavior contract

### Trigger
- The submit listener intercepts **every** form carrying a `data-confirm-message`
  attribute (set on all delete forms regardless of episode status).
- On submit: `preventDefault()` + `stopImmediatePropagation()`, populate the modal body
  from the attribute, show the modal, store the pending form, focus the Cancel button.

### Dismissal (cancel)
- Cancel button click, `Escape` keypress, or overlay backdrop click hides the modal and
  clears the pending form. No submit occurs.
- On any dismissal, focus returns to the Delete button that opened the modal.

### Confirmation
- The Delete button hides the modal and calls `pendingForm.submit()`.
- `HTMLFormElement.submit()` bypasses the submit event, so no re-entrant guard flag is
  needed (contrast with `requestSubmit()`, which re-fires submit and would loop).

### Copy (status-aware, set in the Jinja template)
| Episode status | `data-confirm-message` |
|---|---|
| `done` | "Delete this episode, remove its audio file, and mark the article as read in Wallabag?" |
| `staged`, `failed`, `generating` (orphan) | "Delete this episode and mark the article as read in Wallabag?" |

The done copy mentions mp3/audio removal because that loss is irreversible; all other
statuses only remove the queue row and mark the Wallabag article read.

### Known edge case
When the episode is `generating` **during an active run**, `queue_delete`
(`app/main.py`) cancels the generation task instead of deleting — it redirects with
"Stopping generation..." and leaves the row (now `failed`) in the queue. The modal copy
("Delete this episode...") is slightly inaccurate for this rare path, but it is strictly
better than the prior behaviour (no confirmation at all). Refine later if it becomes a
real source of confusion.

## Accessibility
- Modal container uses `role="dialog"` + `aria-modal="true"`, title referenced via
  `aria-labelledby`.
- Focus moves to the Cancel button on open and returns to the triggering Delete button
  on close.
- `Escape` closes the modal.

## Visual design
- Matches the existing cream/green/tan theme: `--card` background, `--radius`,
  `--shadow`, `--danger` accents on the confirm button (reuses `.btn .btn-stop`).
- Semi-transparent overlay backdrop (`rgba(46,42,36,0.4)`) covering the viewport.
- Centered card; buttons right-aligned in a flex actions row.

## Testing expectations
- Every delete form on the rendered page carries a `data-confirm-message` attribute.
- The done episode's message contains "audio file"; staged/failed messages do not.
- No `data-confirm="true"` attribute remains (the old attribute name is retired).
- The existing delete-route and pipeline tests are unchanged (no backend logic touched).