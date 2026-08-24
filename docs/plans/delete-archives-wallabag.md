---
plan name: delete-archives-wallabag
plan description: Remove episode, mark read
plan status: done
---

## Idea
Make the per-episode Delete button also mark the source article as archived/read in Wallabag (PATCH /api/entries/{wallabag_id}.json with archive=1). User decisions: archive on ALL deletes (staged, failed, done, orphan-generating — every episode row has a wallabag_id), and ABORT the local delete if the Wallabag call fails (episode stays in queue, error flash shown). The generating-during-active-run branch of POST /queue/{id}/delete keeps its current Stop behavior (task.cancel(), no delete, no archive). Note a behavioral consequence: archived articles never appear in list_unread_metadata (archive=0 filter), so a deleted done episode's article will NOT be re-picked by Add Random anymore, even though we still remove its processed_articles dedupe row (harmless; keeps state consistent if the user manually unarchives later).

Implementation shape:
- wallabag.py: add `async def archive(entry_id)` going through `_request` (free OAuth refresh/retry), raising WallabagError on non-2xx.
- db.py: add a small getter for the wallabag_id of an episode (needed because archiving must happen BEFORE the row is deleted).
- pipeline.py: make `delete_item(episode_id, wallabag_client)` async: look up (wallabag_id, status) -> await wallabag_client.archive(wallabag_id) -> only then delete row / unlink mp3 / drop processed_articles row. ValueError semantics preserved.
- main.py queue_delete: await delete_item with app.state.wallabag_client; catch WallabagError -> _redirect("/", error=...).
- Tests: test_wallabag (archive method contract), test_pipeline (existing sync delete_item tests become async with MockTransport client recording PATCH calls; failure test asserts nothing was deleted), test_web (delete-route tests need a fake wallabag client on app.state since lifespan installs a real one; assert archive called, and error flash + intact row on WallabagError; stop-branch test unchanged/no archive).
- UI copy: update the done-episode confirm() text in static/js/app.js to mention the article being marked read/archived in Wallabag.
- Docs: README.md workflow mentions, docs/specs/data-model.md (delete_item op + re-pickability caveat), docs/specs/cancellable-generation.md (delete archives on actual deletes; stop path does not).

## Implementation
- app/wallabag.py: add `async def archive(self, entry_id: int) -> None` sending PATCH {base}/api/entries/{entry_id}.json with data={"archive": "1"} via self._request; raise WallabagError when resp.status_code >= 300; add tests in tests/test_wallabag.py asserting method/path/body, success no-op, and non-2xx raising.
- app/db.py: add get_episode_wallabag_id(conn, episode_id) -> int | None (SELECT wallabag_id FROM episodes WHERE id=?).
- app/pipeline.py: convert delete_item to `async def delete_item(episode_id, wallabag_client)`; before deleting, fetch wallabag_id (raise ValueError not-found first) and `await wallabag_client.archive(wallabag_id)`; only after success perform existing local cleanup (delete_episode row, done-status mp3 unlink best-effort, processed_articles removal); update module + function docstrings.
- app/main.py: queue_delete awaits `delete_item(episode_id, app.state.wallabag_client)`; keep generating-during-active-run stop branch untouched; catch WallabagError separately and redirect with an error flash (no deletion happened).
- tests/test_pipeline.py: make existing delete_item tests async and pass a _make_wallabag MockTransport client whose handler records PATCH requests; add test_archive_called_with_wallabag_id, test_delete_item_archives_for_each_status, and test_archive_failure_leaves_episode_intact (row + mp3 still present). Run pytest.
- tests/test_web.py: install a fake wallabag client on app.state.wallabag_client in all delete-route tests (async archive stub recording calls, plus a raising variant); assert POST /queue/{id}/delete calls archive, and WallabagError yields error flash with row intact; verify stop branch performs no archive call. Run pytest.
- static/js/app.js: update done-episode confirm text to mention marking the article read/archived in Wallabag; README.md workflow lines 95/105/112 updated to say Delete also archives the article in Wallabag.
- docs/specs/data-model.md: document that delete_item archives the Wallabag article first (abort-on-failure) and note archived articles are excluded from future add_random enumeration; docs/specs/cancellable-generation.md: note the delete route now archives on real deletes while the active-run branch still just stops.

## Required Specs
<!-- SPECS_START -->
- data-model
- architecture-and-stack
- config-and-env
- delete-archives-wallabag
<!-- SPECS_END -->