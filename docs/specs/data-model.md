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
- `created_at` TEXT              # staged time
- `generated_at` TEXT            # when audio finished

### processed_articles (dedupe index)
- `wallabag_id` INTEGER PRIMARY KEY
- `episode_id` INTEGER           # nullable
- `processed_at` TEXT

A `wallabag_id` is added here ONLY on successful generation. Deleting a `done`
episode removes its `processed_articles` row, so the article becomes re-pickable
by `add_random`. Removing a `staged`/`failed`/`generating` episode (which never
recorded a row) does not touch `processed_articles`.

## Queue state machine
```
staged --(generate)--> generating --(success)--> done --(delete)--> ∅
                                   \--(fail)--> failed --(retry)--> generating
                                   \--(cancel)--> failed
```
- **staged:** candidate fetched, no audio yet. Deletable via the per-item Delete button. Deleting a staged item does NOT add it to processed_articles (so it can be re-picked later).
- **generating:** being synthesized; a task cancellation (Stop) marks it `failed` ("Cancelled by user"). Deletable via the Delete button only when no run is active (orphan cleanup); during an active run use the Stop button.
- **done:** has audio; appears in the RSS feed. Deletable via the per-item Delete button — this unlinks the mp3 and removes the processed_articles row, making the article re-pickable. A confirm prompt guards the delete (irreversible mp3 loss).
- **failed:** generation error or user cancellation ("Cancelled by user"); retryable. Deletable via the Delete button.

## Queue ops (pipeline layer)
- `add_random(n)`: enumerate unread (Wallabag `archive=0, detail=metadata`) excluding EXCLUDE_TAGS (client-side tag filter) and not in processed_articles; pick n random; insert as `staged` with `est_minutes=reading_time`. Idempotent on wallabag_id (skip if already staged/done). A previously-deleted done episode's article IS eligible again (its processed_articles row was removed).
- `delete_item(id)`: delete a `staged`|`failed`|`generating`|`done` episode. For `done` episodes, also unlink the mp3 at `audio_path` (best-effort) and remove the `processed_articles` row so the article is re-pickable. Raises `ValueError` if the episode is missing or `archived` (non-deletable).
- `stop_generation()`: cancel the active generation task (`app.state.generation_task.cancel()`); the in-flight episode is marked `failed` ("Cancelled by user") by `generate_all`'s CancelledError handler; remaining staged episodes stay `staged`. (Main.py-level op.)
- `generate_all()`: for each staged → status=generating; clean text; synthesize; write audio; set duration_sec; status=done; insert processed_articles. Continue past per-article failures (mark failed, keep going). A task cancellation aborts the run: the in-flight episode is marked `failed` ("Cancelled by user") and the run halts (remaining staged stay staged).
- `clear_queue()`: delete staged|failed episodes (does not touch done episodes or any processed_articles rows).
- `stats()`: total_minutes (sum est_minutes for staged + duration_sec/60 for done), counts by status, current drive_id.