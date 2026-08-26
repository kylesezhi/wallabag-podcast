# Spec: cancellable-generation

Scope: feature

# Spec: cancellable-generation

Scope: feature

# Cancellable Generation & Deletable Generating Episodes

## Goal
A user can stop a generation run that is in progress (aborting the in-flight TTS synthesis immediately), and can delete any episode stuck in the `generating` status — whether it was stopped mid-run or orphaned by a crash/restart.

## Cancellation model

**Single mechanism: asyncio task cancellation.** No flags, no events.

- Generation runs as a real `asyncio.Task` (via `asyncio.create_task`), with its handle stored on `app.state.generation_task`. It is NOT scheduled via FastAPI `BackgroundTasks` (which exposes no cancellable handle).
- Stopping = `app.state.generation_task.cancel()`. This injects `asyncio.CancelledError` at the next `await` inside `generate_all()` — i.e. at the slow/hung `await kokoro_client.synthesize(...)` (or `await wallabag_client.get_entry(...)`).
- `generate_all()` catches `asyncio.CancelledError` explicitly. (`CancelledError` is a `BaseException`, NOT caught by the existing `except Exception:` clause.) On cancel it:
  1. marks the current (in-flight) episode `failed` with error `"Cancelled by user"`,
  2. increments `summary["failed"]`,
  3. `break`s out of the loop — halting the entire run (remaining staged episodes stay `staged` and can be generated later).
- The outer `finally: conn.close()` still runs. `_run_generation`'s own `finally` flips `app.state.generating = False`. The cancelled task returns the partial summary normally (it does not re-raise `CancelledError` to the caller).
- Lifespan shutdown cancels and awaits any lingering generation task so the app exits cleanly.

### Stop semantics (approved decisions)
- **Scope:** halt the ENTIRE run — the current episode AND skip all remaining queued episodes (they stay `staged`).
- **Timing:** immediate — abort the in-flight synthesis, do not let it finish.
- **Stopped episode state:** `failed` with error `"Cancelled by user"` (visible, retryable, deletable via the Delete button). NOT deleted outright.
- `processed_articles` is NOT touched for a cancelled episode (same rule as other failures) — so the article can be re-picked/re-generated later.

## Deletable `generating` episodes

`generating` becomes a deletable status, covering two cases:

1. **Orphan (no active run):** episode stuck in `generating` after a crash/restart. `db.delete_episode` allows `'generating'`. The Delete button always renders on `generating` episodes; with no active run the delete route deletes directly (no Wallabag call). The Archive button also renders and just marks the article read in Wallabag.
2. **Active run, target is the generating episode:** the per-item delete route does NOT delete directly (the loop owns that row). Instead it triggers the same cancellation as the Stop button: `task.cancel()`. The loop marks the episode `failed`, the existing `/queue/status` polling JS reloads the page, and the user then clicks Delete on the now-`failed` row. The Archive button on a generating episode during an active run just archives (no conflict with the loop; no stop).

`archived` / missing episodes remain non-deletable/non-archivable (unchanged ValueError behavior).

## Routes

- `POST /queue/stop` (NEW): if a generation task is active and not done, `task.cancel()` and redirect to `/` with message `"Stopping generation…"`. If no run is active, redirect with error `"No generation run to stop"`.
- `POST /queue/{episode_id}/delete` (UPDATED): for a `generating` target during an active run → trigger stop (cancel task), redirect with message `"Stopping generation…"` (the loop will mark it failed; reload shows the Delete button on the now-failed row). For a `generating` target with no active run → delete directly (orphan cleanup, no Wallabag call). For `staged`/`failed`/`done` → delete locally (no Wallabag call; for `done`, also unlinks the mp3 and deletes the processed_articles row). For `archived`/missing → `ValueError` (unchanged).
- `POST /queue/{episode_id}/archive` (NEW): marks the article as read in Wallabag (`PATCH archive=1`). For any status (staged/failed/generating/done): archive only, no local deletion. During an active run, archiving a generating episode just archives (no stop; no conflict with the loop). For `archived`/missing → `ValueError`.

## UI

- **Global Stop button:** rendered inside the progress card (`#generation-progress`), shown ONLY while `generating` is true. POSTs to `/queue/stop`.
- **Per-item buttons:** each episode shows two side-by-side buttons — **Delete** (POSTs to `/queue/{id}/delete`, styled `.btn-delete`) and **Archive** (POSTs to `/queue/{id}/archive`, styled `.btn-archive`). Both render for `staged`, `failed`, `done`, AND `generating` — unconditionally. Both carry `data-confirm-message` (status-aware) and `data-confirm-label` ("Delete" or "Archive") for the styled confirmation modal. The Delete button's confirm copy for done episodes mentions mp3 removal; the Archive button's copy mentions the episode staying in the podcast.
- **JS:** the existing polling logic in `static/js/app.js` is unchanged (polls `/queue/status` and reloads when `generating` flips false). The modal IIFE intercepts `form[data-confirm-message]`, reads the `data-confirm-label` to set the confirm button text, and submits on confirm. No changes needed for the split.

## Docs
- `docs/specs/data-model.md`: state machine gains `generating --(cancel)--> failed`; queue-ops section adds `stop_generation()`, `archive_item()`, and notes `delete_item()` is sync/no-Wallabag while `archive_item()` is async/Wallabag-only.
- `README.md` "Using the app": document Stop + deleting stuck (orphaned generating) episodes + archiving.

## Acceptance criteria
- Stop button halts an active run; the in-flight episode ends up `failed` ("Cancelled by user"); remaining staged episodes stay `staged` and can be generated later; `processed_articles` untouched for the cancelled episode.
- Every `generating` episode (orphaned or in-flight during a run) shows both a Delete button and an Archive button.
- Delete-on-active-generating triggers stop (does not delete directly); after reload the episode is `failed` with a Delete button. Archive-on-active-generating just archives (no stop; no conflict).
- `delete_item` for `archived`/missing still raises `ValueError`; `done` episodes are deletable (mp3 unlinked, processed_articles row removed, article stays unread).
- `archive_item` marks the article read in Wallabag; nothing local is deleted. A `WallabagError` leaves the episode row, mp3, and dedupe row untouched.
- Deleting a `done` episode removes its mp3 from disk and its processed_articles row; a styled confirmation modal prompts before the delete submits.
- The `archived` status is no longer set by any UI flow; legacy archived rows remain hidden.
- All existing tests pass; new tests cover in-flight cancel, pre-start cancel, orphan delete, stop route (active + inactive), delete-on-active-generating triggers stop, archive route, UI button visibility.