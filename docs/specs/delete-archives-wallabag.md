# Spec: delete-archives-wallabag

Scope: feature

# Feature: Delete archives in Wallabag

The per-episode **Delete** button (POST `/queue/{id}/delete`) marks the source article as archived/read in Wallabag in addition to the existing local cleanup.

## Contract

- **Scope:** every delete archives — `staged`, `failed`, `done`, and orphan `generating` episodes all carry a `wallabag_id`.
- **Ordering:** Wallabag archive happens BEFORE local deletion. If the API call fails, nothing is deleted locally: the episode row (and mp3 for done) stays intact and the UI shows an error flash.
- **API:** `WallabagClient.archive(entry_id)` → `PATCH {WALLABAG_URL}/api/entries/{entry_id}.json` with form field `archive=1`; goes through `_request` (Bearer token + one 401 refresh/retry); raises `WallabagError` subclasses on connection/auth failure and `WallabagError` on non-2xx status.
- **Pipeline:** `pipeline.delete_item(episode_id, wallabag_client)` is async; ValueError semantics preserved for missing/non-deletable rows; after successful archive it performs the existing cleanup (delete row; for done also unlink mp3 best-effort and remove the processed_articles dedupe row).
- **Stop branch unchanged:** deleting a `generating` episode during an active run still triggers `task.cancel()` (Stop) with no delete and no archive call.

## Consequences

- Archived articles are excluded from `list_unread_metadata` (`archive=0`), so a deleted done episode's article will NOT be re-picked by Add Random anymore. The processed_articles row is still removed to keep state consistent if the user manually unarchives later.
- The done-episode confirm() dialog mentions that the article will be marked read/archived in Wallabag.

## Tests

- `tests/test_wallabag.py`: PATCH method/path/body asserted; non-2xx raises.
- `tests/test_pipeline.py`: MockTransport client records archive calls per status; archive failure leaves row + mp3 intact.
- `tests/test_web.py`: fake wallabag client installed on `app.state.wallabag_client`; route calls archive; `WallabagError` yields error redirect with row intact; stop branch makes no archive call.