---
plan name: replace-archive-delete
plan description: Per-episode delete removes mp3
plan status: active
---

## Idea
Replace the bulk "Archive Completed" button with a per-episode "Delete" button on every visible episode. Delete removes the episode DB row, deletes the underlying mp3 file (for done episodes), and removes the processed_articles dedupe row so the Wallabag article becomes re-pickable by "Add Random". The Archive feature is fully removed (button, route, pipeline.archive_completed, db.archive_done_episodes, tests). The cancellable-generation contract is preserved: a Delete aimed at a generating episode during an active run triggers task.cancel() (Stop) instead of deleting. A JS confirm() guards deletes on done episodes (irreversible mp3 loss). Route renamed /queue/{id}/remove -> /queue/{id}/delete; pipeline.remove_item -> pipeline.delete_item; db.delete_episode returns the deleted row's wallabag_id/audio_path/status; new db.delete_processed_article. data-model + cancellable-generation specs and README updated.

## Implementation
- db.py: rewrite delete_episode to return (wallabag_id, audio_path, status) of the deleted row and allow staged|failed|generating|done; add delete_processed_article(conn, wallabag_id); remove archive_done_episodes.
- pipeline.py: replace remove_item with delete_item(episode_id) that deletes the row, unlinks the mp3 at audio_path when status was done (best-effort, swallow FileNotFoundError/IsADirectoryError), and removes the processed_articles row for the episode's wallabag_id; remove archive_completed; update module docstring + imports. Update tests/test_pipeline.py: drop test_archive_completed; convert remove_item tests to delete_item tests; add test_delete_item_done_removes_mp3_and_dedup; keep test_delete_item_missing_raises; flip test_remove_item_done_raises -> test_delete_item_done_succeeds. Run pytest.
- main.py: replace POST /queue/{id}/remove with POST /queue/{id}/delete calling delete_item (keep the generating-during-active-run -> task.cancel() stop behavior); remove POST /queue/archive-completed route; update imports. Update tests/test_web.py: drop test_archive_completed + test_archive_completed_empty; convert remove tests to delete tests; add test_delete_done_removes_mp3_and_dedup using _insert_done_audio + _write_audio_file; add test_delete_done_swallows_missing_mp3; retarget the generating-during-active-run stop test to /delete. Run pytest.
- templates/index.html: remove the Archive Completed button form; replace the per-item remove form with a Delete button posting to /queue/{id}/delete; render Delete on staged/failed/done always and on generating only when not generating; add data-confirm attribute on done-episode delete forms. static/css/style.css: add .btn-delete styling. static/js/app.js: add a submit handler that calls confirm() for forms with [data-confirm] and aborts if cancelled.
- docs/specs/data-model.md: remove archived status + archive_completed op; rename remove_item -> delete_item (allows done, unlinks mp3, removes processed_articles -> re-pickable); update state machine + clear_queue note. docs/specs/cancellable-generation.md: route /remove -> /delete; remove done/archived non-removable clauses; note delete on generating-during-active-run triggers stop; update acceptance criteria. README.md: remove Archive Completed mention; document per-episode Delete button + confirm + re-pickable.

## Required Specs
<!-- SPECS_START -->
- data-model
<!-- SPECS_END -->