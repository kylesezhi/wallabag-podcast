# Spec: cancellable-generation

Scope: feature

# Spec: cancellable-generation

Scope: feature

# Cancellable Generation & Removable Generating Episodes

## Goal
A user can stop a generation run that is in progress (aborting the in-flight TTS synthesis immediately), and can remove any episode stuck in the `generating` status — whether it was stopped mid-run or orphaned by a crash/restart.

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
- **Stopped episode state:** `failed` with error `"Cancelled by user"` (visible, retryable, removable via existing ⊖). NOT deleted outright.
- `processed_articles` is NOT touched for a cancelled episode (same rule as other failures) — so the article can be re-picked/re-generated later.

## Removable `generating` episodes

`generating` becomes a removable status, covering two cases:

1. **Orphan (no active run):** episode stuck in `generating` after a crash/restart. `db.delete_episode` allows `'generating'`. The ⊖ button renders on `generating` episodes when no run is active, and the remove route deletes directly.
2. **Active run, target is the generating episode:** the per-item remove route does NOT delete directly (the loop owns that row). Instead it triggers the same cancellation as the Stop button: `task.cancel()`. The loop marks the episode `failed`, the existing `/queue/status` polling JS reloads the page, and the user then clicks ⊖ on the now-`failed` row.

`done` / `archived` / missing episodes remain non-removable (unchanged ValueError behavior).

## Routes

- `POST /queue/stop` (NEW): if a generation task is active and not done, `task.cancel()` and redirect to `/` with message `"Stopping generation…"`. If no run is active, redirect with error `"No generation run to stop"`.
- `POST /queue/{episode_id}/remove` (UPDATED): for a `generating` target during an active run → trigger stop (cancel task), redirect with message `"Stopping generation…"` (the loop will mark it failed; reload shows ⊖). For a `generating` target with no active run → delete directly (orphan cleanup). For `staged`/`failed` → unchanged direct delete. For `done`/`archived`/missing → unchanged ValueError.

## UI

- **Global Stop button:** rendered inside the progress card (`#generation-progress`), shown ONLY while `generating` is true. POSTs to `/queue/stop`.
- **Per-item ⊖ button:** now renders for `staged`, `failed`, AND `generating` — but the `generating` case is gated on `not generating` (no active run). During an active run the generating episode shows just its status badge (use the global Stop button).
- **No JS change:** `static/js/app.js` already polls `/queue/status` and reloads when `generating` flips false. After a stop, the cancelled episode is already `failed` in the DB by the time `generating` goes false, so the reload shows it with ⊖.

## Docs
- `docs/specs/data-model.md`: state machine gains `generating --(cancel)--> failed`; queue-ops section adds `stop_generation()` and notes `remove_item()` allows `generating`.
- `README.md` "Using the app": document Stop + removing stuck (orphaned generating) episodes.

## Acceptance criteria
- Stop button halts an active run; the in-flight episode ends up `failed` ("Cancelled by user"); remaining staged episodes stay `staged` and can be generated later; `processed_articles` untouched for the cancelled episode.
- Orphaned `generating` episode (no active run) shows ⊖ and is deletable.
- Remove-on-active-generating triggers stop (does not delete directly); after reload the episode is `failed` with ⊖.
- `remove_item` for `done`/`archived`/missing still raises ValueError (unchanged).
- All existing tests pass; new tests cover in-flight cancel, pre-start cancel, orphan remove, stop route (active + inactive), remove-on-active-generating triggers stop, UI button visibility.