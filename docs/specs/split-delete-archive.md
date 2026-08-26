# Spec: split-delete-archive

Scope: feature

# Spec: split-delete-archive

Scope: feature

# Feature: Split Delete / Archive concerns

The per-episode row exposes **two independent actions** instead of one combined
"Delete" button. Each button performs exactly one of the two operations the
former combined button did atomically.

## Buttons

- **Delete** (`POST /queue/{id}/delete`) — podcast cleanup only.
- **Archive** (`POST /queue/{id}/archive`) — Wallabag mark-read only.

Both buttons render on every visible status (`staged`, `failed`, `done`,
`generating`), side-by-side, with short labels and descriptive
`title`/`aria-label` ("Delete from podcast" / "Archive in Wallabag").

## Delete contract (podcast cleanup, no Wallabag)

`pipeline.delete_item(episode_id: int) -> None` is **sync** (no I/O: only sync
sqlite + filesystem). It does NOT call Wallabag.

- Look up `status` (raise `ValueError` if missing or legacy `archived`).
- `delete_episode` the row → `(wallabag_id, audio_path, status)`.
- For `done`: `delete_processed_article(wallabag_id)` (dedupe row removed →
  article becomes re-pickable by `add_random`) and best-effort `unlink` of
  `audio_path` (swallow `OSError`).
- For `staged`/`failed`/`generating`: row deletion only (no mp3, no dedupe row).

The article stays **unread** in Wallabag. Because the `processed_articles`
dedupe row is removed (done) and the article is still `archive=0`, `add_random`
can re-stage it.

## Archive contract (Wallabag mark-read, no local deletion)

`pipeline.archive_item(episode_id: int, wallabag_client: WallabagClient) -> None`
is **async** (awaits the Wallabag PATCH).

- Look up `wallabag_id` + `status` (raise `ValueError` if missing or legacy
  `archived`).
- `await wallabag_client.archive(wallabag_id)` →
  `PATCH {WALLABAG_URL}/api/entries/{wallabag_id}.json` with `archive=1`.
- On `WallabagError`: propagate (nothing happened locally; episode intact).
- On success: **no local change** — the episode row, mp3, and RSS entry all
  stay. The article is now `archive=1` in Wallabag (excluded from
  `list_unread_metadata`, so `add_random` won't re-enumerate it).

`WallabagClient.archive` is unchanged (idempotent PATCH).

## Routes

- `POST /queue/{id}/delete` (`queue_delete`):
  - `generating` + active run → `task.cancel()` (Stop), redirect
    `"Stopping generation..."` (loop owns the row; no delete, no archive).
    Unchanged from prior behavior.
  - Else call sync `delete_item(episode_id)`. Catch `ValueError` → error flash.
    No `WallabagError` is possible (no Wallabag call). Success flash:
    `"Removed from podcast (article stays unread in Wallabag)"`.
- `POST /queue/{id}/archive` (`queue_archive`):
  - No stop branch — archiving does not conflict with the generation loop.
  - `await archive_item(episode_id, app.state.wallabag_client)`. Catch
    `ValueError` → error flash. Catch `WallabagError` → error flash
    `"Could not mark article as read in Wallabag: {exc}"` (episode intact).
    Success flash: `"Article marked read in Wallabag (episode kept in podcast)"`.

## Confirmation modal

The styled modal (spec: `delete-confirmation-modal`) guards **both** actions.
Each form carries `data-confirm-message` (status-aware) and
`data-confirm-label` ("Delete" or "Archive"). The existing JS reads these
attributes and sets the confirm-button label + body copy. No JS change required.

| Action  | Status                   | `data-confirm-message`                                                                  |
| ------- | ------------------------ | ---------------------------------------------------------------------------------------- |
| Delete  | done                     | Delete this episode and remove its audio file? The article stays unread in Wallabag.    |
| Delete  | staged/failed/generating | Delete this episode from the podcast? The article stays unread in Wallabag.              |
| Archive | all                      | Mark this article as read in Wallabag? The episode stays in your podcast.                |

## Consequences

- The former `delete-archives-wallabag` feature is **superseded**: Delete no
  longer archives; archiving is a separate, non-destructive (locally) action.
- `delete_item` loses its `wallabag_client` parameter and becomes sync.
- An archived article is excluded from `add_random` enumeration (`archive=0`
  filter), so an episode whose article was archived will not be re-staged by
  Add Random even if its `processed_articles` dedupe row is later removed.
- Doing both actions (Archive then Delete, or Delete then... the article is
  unread so Archive is still possible via Wallabag) is the user's explicit
  choice — the two are fully decoupled.

## Tests

- `tests/test_pipeline.py`: `delete_item` tests converted to sync, no
  wallabag client, no archive assertions; `archive_item` tests (async, mock
  client records PATCH, WallabagError leaves row intact, no local deletion).
- `tests/test_web.py`: delete-route tests drop archive expectations + new flash;
  archive-route tests (success, WallabagError, ValueError, no local deletion);
  button tests assert two forms per row with correct `data-confirm-*`.
- `tests/test_wallabag.py`: `archive` method tests unchanged.