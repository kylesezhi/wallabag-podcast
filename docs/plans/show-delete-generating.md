---
plan name: show-delete-generating
plan description: Show remove button mid-run
plan status: done
---

## Idea
Fix issue "Delete button is not showing on episodes that are in Generating state". The stop-generating feature intentionally hid the per-item Delete button on the in-flight generating episode while a run was active (template gated it on `not generating`); the user has approved changing this so Delete is ALWAYS visible on generating episodes. No backend change is required: POST /queue/{id}/delete (app/main.py queue_delete) already branches correctly — a generating target during an active run cancels the task (redirects with "Stopping generation…"; loop marks the episode failed "Cancelled by user", polling JS reloads, then the user can delete the failed row), and a generating orphan (no active run) archives in Wallabag then deletes directly. Changes are template visibility + flipping one web test + doc/README wording updates.

Design decisions confirmed by user: always show Delete on generating episodes; during-run click = cancel-then-delete flow (existing route behavior). The global Stop Generating button stays as-is.

## Implementation
- templates/index.html line 77: replace `{% if ep.status in ("staged", "failed", "done") or (ep.status == "generating" and not generating) %}` with `{% if ep.status in ("staged", "failed", "done", "generating") %}` so the Delete form renders on every visible queue status; keep the done-episode data-confirm guard untouched
- tests/test_web.py: flip test_delete_button_hidden_for_generating_during_run (~line 727) into test_delete_button_shown_for_generating_during_run — keep inserting a generating episode and setting app.state.generating = True, but assert action="/queue/{episode_id}/delete" IS present alongside the badge; leave test_delete_button_shown_for_orphan_generating as-is
- Run the full suite via `uv run python -m pytest -q` (all existing tests must pass, including test_delete_active_generating_triggers_stop which proves the during-run click path works)
- Docs: docs/specs/data-model.md line 54 (generating bullet) — 'Deletable via the Delete button only when no run is active' → always deletable; during an active run the delete route triggers Stop (task.cancel) first, marking the episode failed for removal; docs/specs/cancellable-generation.md lines 37-38 + 50 (UI section: drop the `not generating` gating note) + acceptance criteria line 58 (orphan shows Delete → every generating episode shows Delete)
- README.md 'Using the app' ~lines 111-114: update stuck-episode guidance — Delete button now always appears; clicking it during an active run stops generation (article marked failed, then delete again); Stop Generating button remains available

## Required Specs
<!-- SPECS_START -->
- data-model
- architecture-and-stack
- config-and-env
- show-delete-generating
<!-- SPECS_END -->