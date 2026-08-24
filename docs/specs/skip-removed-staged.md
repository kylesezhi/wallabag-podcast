# Spec: skip-removed-staged

Scope: feature

# Spec: skip-removed-staged

Scope: feature

# Skip Episodes Removed Mid-Run

## Goal
Removing a `staged` episode (⊖ button) during an active generation run must prevent that article from being generated later in the same run.

## Root cause (fixed behavior)
`generate_all` snapshots staged episodes once at run start and iterates the snapshot. Previously, when the loop reached an ID whose row had been deleted mid-run, it still fetched the article, synthesized TTS, wrote an orphan `data/audio/{id}.mp3`, and inserted a stale `processed_articles` row pointing at the deleted episode.

## Behavior
- At the top of each loop iteration (before `set_episode_generating`), re-check `get_episode_status(conn, episode_id)`.
- If the row is missing (deleted mid-run) or its status is no longer `staged`: log info ("removed mid-run, skipping"), decrement `summary["total"]`, and `continue`.
- The skipped episode produces NO TTS call, NO mp3 file, NO processed_articles row, and counts as neither done nor failed.
- Race safety: asyncio is single-threaded and there is no await between the existence check and `set_episode_generating`, so a delete cannot interleave.

## Unchanged
- Delete on the in-flight `generating` episode during an active run still triggers whole-run Stop (existing cancellable-generation flow).
- Orphan cleanup of mp3 files from runs affected before this fix is out of scope.

## Acceptance criteria
- With two staged episodes, deleting one mid-run means only the survivor is synthesized; the deleted one has no DB row, no processed_articles entry, and no audio file.
- Summary counts reflect only surviving episodes (total decremented for removed ones; done/failed unchanged by removals).
- All existing tests pass.