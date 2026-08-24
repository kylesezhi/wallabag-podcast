# Spec: retry-on-generate

Scope: feature

# Retry Failed Episodes on Generate

## Behavior
Clicking **Generate Audio** (`POST /queue/generate`) sweeps all previously-`failed` episodes back into the run:

- Before the emptiness gate, the route calls `db.reset_failed_to_staged(conn)`: `UPDATE episodes SET status='staged', error=NULL WHERE status='failed'`.
- All failed episodes are retried — including `"Skipped: ..."` (short-text) and `"Cancelled by user"` ones. A deterministic skip simply re-fails cheaply (Wallabag fetch + clean, no TTS call).
- Retried episodes run alongside newly staged episodes in a single pass ordered by id (oldest first). Each is processed exactly once per run.
- If nothing is staged after the reset (truly empty queue), the existing redirect error "No staged articles to generate" is shown unchanged.
- The generating-in-progress guard is unchanged.

## Outcomes
- Retry success → `done` (mp3 written, duration set, `processed_articles` row added), same as any staged episode.
- Retry failure → re-marked `failed` with the fresh error text; remains retryable on the next Generate click.
- Cancel mid-retry → episode marked `failed` ("Cancelled by user") by the existing cancellation path; next Generate retries it again.

## UI
- Drive-status headline shows **Ready to generate** whenever `stats.staged + stats.failed > 0` (previously a failed-only queue claimed "Ready to listen"/"Your queue is empty").
- Progress card already counts failed episodes in its total; no change needed.
- Delete buttons/flash messages unaffected.

## State machine
`failed --(Generate Audio)--> staged --(run)--> generating --> done|failed` — implements the previously documented-but-unimplemented `failed --(retry)--> generating` edge.