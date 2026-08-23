---
plan name: stop-generating
plan description: Cancellable generation and removable stuck episodes
plan status: done
---

## Idea
Add cooperative + immediate cancellation to the generation run via standard asyncio task cancellation, and make `generating` episodes removable (for both the stopped mid-run episode and orphaned/crash-recovery cases).

## Problem
- `generate_all()` runs as a FastAPI `BackgroundTask` — no task handle is exposed, so it can't be cancelled today. A hung Kokoro synthesis cannot be aborted.
- `delete_episode` / the ⊖ button only allow `staged`|`failed`; an episode stuck in `generating` (e.g. after a crash/restart) is permanently unremovable without manual DB intervention.

## Design (approved)
Single cancellation mechanism: `asyncio.create_task` stores a handle on `app.state.generation_task`; the Stop route calls `task.cancel()`, which injects `CancelledError` at the next `await` inside `generate_all` (i.e. at the slow `await kokoro_client.synthesize(...)`). The loop catches `asyncio.CancelledError` explicitly (it's a `BaseException`, not caught by the existing `except Exception:`), marks the current episode `failed` ("Cancelled by user"), increments `failed`, and `break`s to halt the run. `_run_generation`'s `finally` flips `app.state.generating = False`. No event, no flag.

`generating` becomes a removable status: `db.delete_episode` allows `generating` (for orphan cleanup). The ⊖ button renders on `generating` episodes only when no run is active (orphan case). During an active run, the generating episode shows just its badge (use the global Stop button). The per-item remove route, when targeting a `generating` episode while a run IS active, triggers stop (same as Stop button) instead of deleting — the loop marks it `failed`, page reloads via existing polling, user then clicks ⊖.

New `POST /queue/stop` route: cancels the active generation task, redirects with "Stopping generation…". Errors if no run is active.

Existing JS (`static/js/app.js`) already reloads when `generating` flips false; after stop the episode is already `failed` in DB by then, so the reload shows it with ⊖. No JS change.

Docs: update `docs/specs/data-model.md` (state machine: `generating --(cancel)--> failed`; new `stop_generation` op; `remove_item` allows generating) and `README.md` (Using the app → Stop + removing stuck episodes).

## Tasks (sequential, one Developer task at a time; reviewed by Architect before next)
1. **pipeline + db**: `delete_episode` allow `generating`; `generate_all` catches `asyncio.CancelledError`, marks failed, breaks; pipeline tests.
2. **main.py**: `asyncio.create_task` scheduling + `app.state.generation_task`; lifespan shutdown cleanup; `POST /queue/stop`; update `queue_remove` for generating; web tests.
3. **template + docs**: Stop button + ⊖ visibility logic; `data-model.md` + `README.md` updates; UI tests.

## Implementation
- pipeline + db: db.delete_episode allow 'generating' status; generate_all add `except asyncio.CancelledError` clause that marks the current episode failed ('Cancelled by user'), increments summary['failed'], and breaks to halt the run
- pipeline tests: add test for in-flight cancel (CancelledError raised at kokoro synthesize await -> current episode failed with 'Cancelled' error, remaining staged episodes stay staged, summary counts correct) and test for pre-start cancel (CancelledError before first await -> no episode marked generating, summary total=0)
- main.py: replace BackgroundTasks.add_task(_run_generation) with asyncio.create_task storing handle on app.state.generation_task; add POST /queue/stop route that cancels the active task (errors if none active)
- main.py: update queue_remove so a generating target during an active run triggers stop instead of delete, and deletes directly when no run is active (orphan); lifespan shutdown cancels/awaits lingering task
- web tests: stop route active + inactive; remove-on-active-generating triggers stop; remove-orphan-generating deletes directly
- template + docs: add global Stop Generating button in the progress card (shown only while generating); render ⊖ on generating episodes only when no run is active; update docs/specs/data-model.md state machine and queue-ops section; update README.md Using-the-app section; add UI tests for button visibility

## Required Specs
<!-- SPECS_START -->
- data-model
- architecture-and-stack
- config-and-env
- cancellable-generation
<!-- SPECS_END -->