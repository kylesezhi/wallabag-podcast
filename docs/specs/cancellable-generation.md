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

1. **Orphan (no active run):** episode stuck in `generating` after a crash/restart. `db.delete_episode` allows `'generating'`. The Delete button always renders on `generating` episodes; with no active run the delete route archives the article in Wallabag then deletes directly.
2. **Active run, target is the generating episode:** the per-item delete route does NOT delete directly (the loop owns that row). Instead it triggers the same cancellation as the Stop button: `task.cancel()`. No Wallabag archive call is made (nothing was deleted). The loop marks the episode `failed`, the existing `/queue/status` polling JS reloads the page, and the user then clicks Delete on the now-`failed` row.

`archived` / missing episodes remain non-deletable (unchanged ValueError behavior; no archive call); `done` episodes ARE deletable (archives in Wallabag, unlinks mp3 + removes the dedupe row).

## Routes

- `POST /queue/stop` (NEW): if a generation task is active and not done, `task.cancel()` and redirect to `/` with message `"Stopping generation…"`. If no run is active, redirect with error `"No generation run to stop"`.
- `POST /queue/{episode_id}/delete` (UPDATED): for a `generating` target during an active run → trigger stop (cancel task), redirect with message `"Stopping generation…"` (the loop will mark it failed; reload shows the Delete button on the now-failed row; no archive call). For a `generating` target with no active run → archive in Wallabag, then delete directly (orphan cleanup). For `staged`/`failed`/`done` → archive in Wallabag first (abort-on-failure: a `WallabagError` redirects with an error and nothing is deleted), then direct delete (for `done`, also unlinks the mp3 and deletes the processed_articles row). For `archived`/missing → `ValueError` (unchanged, no archive call).

## UI

- **Global Stop button:** rendered inside the progress card (`#generation-progress`), shown ONLY while `generating` is true. POSTs to `/queue/stop`.
- **Per-item Delete button:** renders for `staged`, `failed`, `done`, AND `generating` — unconditionally (the earlier `not generating` gating was removed so the button is always visible; during an active run clicking it triggers the stop flow described above). Done-episode delete forms carry a `data-confirm` attribute; a small JS handler calls `confirm()` before submitting (guards irreversible mp3 loss).
- **JS:** the existing polling logic in `static/js/app.js` is unchanged (polls `/queue/status` and reloads when `generating` flips false). A new small IIFE adds the confirm guard for `form[data-confirm="true"]`. After a stop, the cancelled episode is already `failed` in the DB by the time `generating` goes false, so the reload shows it with a Delete button.

## Docs
- `docs/specs/data-model.md`: state machine gains `generating --(cancel)--> failed`; queue-ops section adds `stop_generation()` and notes `delete_item()` allows `generating` AND `done`.
- `README.md` "Using the app": document Stop + deleting stuck (orphaned generating) episodes.

## Acceptance criteria
- Stop button halts an active run; the in-flight episode ends up `failed` ("Cancelled by user"); remaining staged episodes stay `staged` and can be generated later; `processed_articles` untouched for the cancelled episode.
- Every `generating` episode (orphaned or in-flight during a run) shows a Delete button.
- Delete-on-active-generating triggers stop (does not delete directly); after reload the episode is `failed` with a Delete button.
- `delete_item` for `archived`/missing still raises `ValueError` (no archive call); `done` episodes are deletable (mp3 unlinked, processed_articles row removed).
- Every real delete archives the article in Wallabag first (`PATCH archive=1`); a failed archive call leaves the episode row, mp3, and dedupe row untouched and the UI shows an error flash.
- Deleting a `done` episode removes its mp3 from disk and its processed_articles row; a JS `confirm()` prompts before the delete submits.
- The `archived` status is no longer set by any UI flow; legacy archived rows remain hidden.
- All existing tests pass; new tests cover in-flight cancel, pre-start cancel, orphan delete, stop route (active + inactive), delete-on-active-generating triggers stop, UI button visibility.