# Spec: show-delete-generating

Scope: feature

# Spec: show-delete-generating

Scope: feature

# Always-Visible Delete on Generating Episodes

## Decision
The per-item Delete (⊖) button renders on **every** visible queue status — `staged`, `failed`, `done`, AND `generating` — unconditionally. The previous gating (`ep.status == "generating" and not generating`, which hid the button during an active run) is removed.

Rationale: hiding Delete on the in-flight episode read as a bug ("Delete button not showing on generating episodes"); the backend already handles both branches safely, so the button can always be offered.

## Behavior by run state
- **No active run (orphan generating):** delete route archives the article in Wallabag (`PATCH archive=1`), then deletes the row directly. Unchanged.
- **Active run, target is the generating episode:** delete route does NOT delete directly — it triggers Stop (`task.cancel()`), redirects with "Stopping generation…". The loop marks the episode `failed` ("Cancelled by user"), remaining staged stay `staged`; `/queue/status` polling reloads the page; the user then clicks Delete on the now-`failed` row. No archive call in this branch. Unchanged route behavior — only its discoverability changes.
- Global "Stop Generating" button remains in the progress card as an alternative control.

## Touch points
- `templates/index.html`: visibility condition simplifies to `{% if ep.status in ("staged", "failed", "done", "generating") %}`. The done-episode `data-confirm` guard is untouched; no JS change (polling/reload already covers the stop→failed→delete flow).
- `tests/test_web.py`: the during-run case flips from hidden to shown (`test_delete_button_shown_for_generating_during_run`); orphan case unchanged.

## Acceptance criteria
- A generating episode shows its Delete button both during an active run and when orphaned.
- Clicking Delete on the in-flight episode during a run stops the run (no direct delete, no archive call); after reload the episode is `failed` with a working Delete button.
- Orphaned generating episodes delete directly after a successful Wallabag archive.
- All existing tests pass with the flipped during-run visibility test.