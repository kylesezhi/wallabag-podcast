---
plan name: retry-failed-episodes
plan description: Generate Audio sweeps failed rows
plan status: done
---

## Idea
Failed episodes are currently terminal: POST /queue/generate (app/main.py queue_generate) bails out with "No staged articles to generate" when no staged rows exist, and pipeline.generate_all only selects status='staged' rows, so a failed episode can never be retried — it can only sit in the queue or be deleted. docs/specs/data-model.md already documents the state machine `failed --(retry)--> generating` and calls failed "retryable", but nothing implements it.

Goal: clicking Generate Audio retries ALL previously-failed episodes (including "Skipped:" ones and "Cancelled by user" ones), alongside any newly staged episodes, in one run ordered by id.

Design (user approved retry-all):
- db.py: new helper `reset_failed_to_staged(conn) -> int` executing `UPDATE episodes SET status='staged', error=NULL WHERE status='failed'`; commit; return rowcount.
- app/main.py queue_generate: before the has_staged_episodes gate, call reset_failed_to_staged(conn) on the same connection so failed rows join the run; if still nothing staged (truly empty queue) keep the existing error redirect. Keep the generating-in-progress guard as-is.
- templates/index.html drive-status line 9: `{% elif stats.staged > 0 %}Ready to generate` becomes `{% elif stats.staged + stats.failed > 0 %}Ready to generate` so a failed-only queue doesn't claim "Ready to listen"/"queue is empty". Progress card total already counts failed, no change needed there.
- Docs touch-ups: README.md "Using the app" (~lines 100-115) note that Generate Audio retries failed episodes; docs/specs/data-model.md queue-ops section documents reset-on-generate semantics for generate_all's caller.
- Tests:
  - tests/test_web.py: update/keep test_generate_no_staged (empty DB still errors); add test that a failed-only queue starts generation (generate_all patched); add test that generate with mixed staged+failed resets failed rows to staged before the run starts; add home-page assertion that drive-status shows "Ready to generate" when only failed episodes exist.
  - tests/test_pipeline.py: unit test reset_failed_to_staged (flips statuses, clears error, returns count, leaves done/generating/archived untouched).
Acceptance: with only failed episodes present, clicking Generate Audio runs generation over them; successes become done (mp3 + processed_articles row), failures re-marked failed with fresh error text; staged+failed mixes process everything exactly once; empty queue still shows the existing error flash.

## Implementation
- Add reset_failed_to_staged(conn) -> int helper to app/db.py (UPDATE episodes SET status='staged', error=NULL WHERE status='failed'; commit; return rowcount)
- Update queue_generate in app/main.py to call reset_failed_to_staged(conn) on its connection before the has_staged_episodes gate so failed-only queues start a run
- Update templates/index.html drive-status condition from stats.staged > 0 to stats.staged + stats.failed > 0
- Add pipeline unit test for reset_failed_to_staged in tests/test_pipeline.py
- Add web tests in tests/test_web.py: failed-only queue starts generation, mixed queue sweeps failed into run, home shows 'Ready to generate' for failed-only queue
- Run just test / pytest suite and fix fallout (test_generate_no_staged should still pass)
- Update README.md Using-the-app section and docs/specs/data-model.md to describe retry-on-generate

## Required Specs
<!-- SPECS_START -->
- data-model
- architecture-and-stack
- config-and-env
- retry-on-generate
<!-- SPECS_END -->