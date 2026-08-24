---
plan name: skip-removed-staged
plan description: Skip removed episodes mid-run
plan status: done
---

## Idea
Fix bug: removing a staged article (⊖) during an active generation run does NOT stop it from being generated. Root cause: `generate_all` (app/pipeline.py) snapshots all staged episodes once via `get_staged_episodes(conn)` at run start and iterates that stale list. `queue_delete` correctly archives + deletes the staged DB row mid-run, but when the loop reaches the deleted ID it still: marks generating via blind UPDATE (no-op), fetches the article from Wallabag, synthesizes TTS (the observed wasted generation), writes an orphan `data/audio/{id}.mp3`, calls set_episode_done (no-op), and inserts a stale `processed_articles` dedupe row pointing at the deleted episode.

Fix: at the top of each loop iteration in `generate_all`, re-check the episode row via `get_episode_status(conn, episode_id)`; if it returns None (deleted mid-run) or a non-`staged` status, skip the episode (`continue`, log info). This is race-free because asyncio is single-threaded and there is no await between the check and `set_episode_generating`. Counting semantics: a removed-mid-run episode counts as neither done nor failed — decrement `summary["total"]` so counts stay consistent (it never appears in done/failed/skipped).

Out of scope / unchanged: delete-on-generating-during-run still triggers whole-run Stop (existing designed behavior); orphan mp3 cleanup from previously affected runs not attempted; no UI change needed.

Tests (tests/test_pipeline.py, matching existing conventions `_make_wallabag`/`_make_kokoro`/`_insert_episode`/monkeypatched measure_duration): new test where the wallabag handler deletes a second staged episode when synthesizing the first — assert second episode never synthesized (kokoro handler call count), its DB row stays absent, no processed_articles row for it, no mp3 file written for it, summary reflects only surviving episodes (e.g. total=1, done=1).

Docs: docs/specs/data-model.md generate_all bullet gains "episodes deleted mid-run are skipped (not generated, not counted)". README needs no change (behavior now matches user expectation).

## Implementation
- app/db.py: confirm get_episode_status returns None for missing rows (already does); optionally add a tiny helper or reuse as-is — no schema change
- app/pipeline.py generate_all: inside the for-loop, before set_episode_generating, re-check get_episode_status(conn, episode_id); if None or != 'staged', logger.info('Episode %s removed mid-run, skipping') and summary['total'] -= 1; continue
- tests/test_pipeline.py: add test_generate_all_skips_episode_removed_midrun — two staged episodes; wallabag handler deletes ep 2's row when fetching ep 1; assert kokoro called once, ep 2 absent from episodes + processed_articles, no data/audio/{ep2}.mp3, summary == total/done adjusted
- Run full suite: uv run python -m pytest -q (all existing tests incl. cancellation + web tests must pass)
- Docs: update docs/specs/data-model.md queue-ops generate_all bullet to note mid-run removals are skipped and uncounted

## Required Specs
<!-- SPECS_START -->
- data-model
- architecture-and-stack
- config-and-env
- skip-removed-staged
<!-- SPECS_END -->