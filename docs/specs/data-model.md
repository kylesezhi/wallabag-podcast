# Spec: data-model

Scope: repo

# Data Model &amp; Queue State Machine

SQLite DB at `data/podcast.db`. Single user.

## Tables

### settings (key/value)
- `key` TEXT PRIMARY KEY
- `value` TEXT
- `updated_at` TEXT (ISO)

Keys: `articles_per_drive` (int), `voice` (str), `automation_enabled` (bool, false in v1), `automation_time` (str e.g. "07:00").
Defaults seeded from .env on first run; updated via the settings UI.

### episodes
- `id` INTEGER PRIMARY KEY AUTOINCREMENT
- `wallabag_id` INTEGER UNIQUE   # one episode per Wallabag article
- `title` TEXT
- `source` TEXT                  # Wallabag domain_name
- `url` TEXT                     # original article url
- `status` TEXT                  # staged | generating | done | failed (archived is a legacy status retained for existing rows but no longer set by any UI flow)
- `audio_path` TEXT              # data/audio/{id}.mp3 (nullable until done)
- `duration_sec` INTEGER         # real MP3 duration via mutagen (nullable until done)
- `est_minutes` INTEGER          # Wallabag reading_time, cached for staged stats
- `language` TEXT                # Wallabag language code
- `error` TEXT                   # last failure reason if failed
- `drive_id` INTEGER             # groups episodes of one generate run (nullable)
- `progress_done` INTEGER        # TTS chunks synthesized so far (generating only; nullable)
- `progress_total` INTEGER       # total TTS chunks for the current generation (nullable)
- `created_at` TEXT              # staged time
- `generated_at` TEXT            # when audio finished

### processed_articles (dedupe index)
- `wallabag_id` INTEGER PRIMARY KEY
- `episode_id` INTEGER           # nullable
- `processed_at` TEXT

A `wallabag_id` is added here ONLY on successful generation. Deleting a `done`
episode removes its `processed_articles` row (state consistency) AND the article
stays unread in Wallabag — since the dedupe row is removed, the article becomes
re-pickable by `add_random`. Archiving an episode (separate action) marks the
article read in Wallabag (`archive=1`), which excludes it from
`list_unread_metadata` so `add_random` won't re-enumerate it regardless of
whether its `processed_articles` row exists. Removing a `staged`/`failed`/
`generating` episode (which never recorded a row) does not touch
`processed_articles`.

## Queue state machine
```
staged --(generate)--> generating --(success)--> done --(delete)--> ∅
                                    \--(fail)--> failed --(retry)--> generating
                                    \--(cancel)--> failed
done --(archive)--> done  (article marked read in Wallabag; episode stays)
staged --(archive)--> staged  (article marked read; episode stays)
```
- **staged:** candidate fetched, no audio yet. Deletable via the per-item Delete button (removes the row; article stays unread, re-pickable by add_random). Archivable via the per-item Archive button (marks article read in Wallabag; episode stays queued).
- **generating:** being synthesized; a task cancellation (Stop) marks it `failed` ("Cancelled by user"). Deletable via the Delete button, which always renders; during an active run the delete route triggers Stop first (`task.cancel()`, marking the episode `failed` for removal), while an orphan (no active run) is deleted directly. Archivable via the Archive button at any time (no conflict with the generation loop).
- **done:** has audio; appears in the RSS feed. Deletable via the per-item Delete button — this unlinks the mp3, removes the processed_articles row, and the article stays unread (re-pickable by add_random). Archivable via the per-item Archive button — marks the article read in Wallabag; the episode, mp3, and RSS entry all stay.
- **failed:** generation error or user cancellation ("Cancelled by user"); retryable — clicking Generate Audio sweeps all failed episodes back to `staged` (error cleared) so they join the next run. Deletable via the Delete button. Archivable via the Archive button.

## Queue ops (pipeline layer)
- `add_random(n)`: enumerate unread (Wallabag `archive=0, detail=metadata`) excluding EXCLUDE_TAGS (client-side tag filter) and not in processed_articles; pick n random; insert as `staged` with `est_minutes=reading_time`. Idempotent on wallabag_id (skip if already staged/done). Articles deleted via `delete_item` stay unread and become re-pickable (dedupe row removed). Articles archived via `archive_item` are marked read and excluded from enumeration.
- `delete_item(id)`: DELETE the episode row locally (sync, no Wallabag call, no network I/O). For `done` episodes also unlink the mp3 at `audio_path` (best-effort) and remove the `processed_articles` row so the article becomes re-pickable by `add_random`. Raises `ValueError` if the episode is missing or `archived` (non-deletable). The article stays unread in Wallabag.
- `archive_item(id)`: mark the episode's article as read in Wallabag (`PATCH /api/entries/{wallabag_id}.json`, `archive=1`). Does NOT delete anything locally — the episode row, mp3, and processed_articles row are all left intact. On `WallabagError` the error propagates and nothing local changed. Raises `ValueError` if the episode is missing or `archived` (non-archivable).
- `stop_generation()`: cancel the active generation task (`app.state.generation_task.cancel()`); the in-flight episode is marked `failed` ("Cancelled by user") by `generate_all`'s CancelledError handler; remaining staged episodes stay `staged`. (Main.py-level op.)
- `generate (POST /queue/generate)`: FIRST reset all `failed` episodes to `staged` (`reset_failed_to_staged`, error cleared), then start the run if anything is staged — a failed-only queue therefore generates instead of erroring; an empty queue still shows "No staged articles to generate".
- `generate_all()`: for each staged → status=generating; clean text; split into bounded chunks (`KOKORO_MAX_CHUNK_CHARS`, sentence boundaries); synthesize each chunk with ONE automatic retry on KokoroError, appending bytes to `data/audio/{id}.mp3.part` and persisting `progress_done`/`progress_total` around every chunk; on completion atomically rename the part file to `{id}.mp3`; duration = sum of per-chunk measurements (fallback: est_minutes when any chunk is unparseable); status=done + insert processed_articles. A failed or cancelled episode has its `.part` file removed (best-effort) — no partial audio is ever served or kept. Continue past per-article failures (mark failed, keep going). A task cancellation aborts the run: the in-flight episode is marked `failed` ("Cancelled by user") and the run halts (remaining staged stay staged). An episode removed mid-run while still `staged` is skipped when its turn comes: no TTS call, no audio file, no processed_articles row — it counts as neither done nor failed (`summary.total` is decremented).
- `clear_queue()`: delete staged|failed episodes (does not touch done episodes or any processed_articles rows).
- `stats()`: total_minutes (sum est_minutes for staged + duration_sec/60 for done), counts by status, current drive_id.