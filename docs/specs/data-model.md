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
- `status` TEXT                  # staged | generating | done | failed | archived
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

A `wallabag_id` is added here ONLY on successful generation, and is never removed by local archive/delete — so an article is never re-picked as a random candidate.

## Queue state machine
```
staged --(generate)--> generating --(success)--> done --(archive)--> archived
                                   \--(fail)--> failed --(retry)--> generating
```
- **staged:** candidate fetched, no audio yet. Removable via per-item remove (the ⊖ button). Removing a staged item does NOT add it to processed_articles (so it can be re-picked later).
- **done:** has audio; appears in the RSS feed.
- **failed:** generation error; retryable.
- **archived:** hidden locally, off the feed; audio kept on disk; stays in processed_articles.

## Queue ops (pipeline layer)
- `add_random(n)`: enumerate unread (Wallabag `archive=0, detail=metadata`) excluding EXCLUDE_TAGS (client-side tag filter) and not in processed_articles; pick n random; insert as `staged` with `est_minutes=reading_time`. Idempotent on wallabag_id (skip if already staged/done).
- `remove_item(id)`: delete `staged`|`failed` episode (does not touch processed_articles for staged/failed).
- `generate_all()`: for each staged → status=generating; clean text; synthesize; write audio; set duration_sec; status=done; insert processed_articles. Continue past per-article failures (mark failed, keep going).
- `archive_completed()`: status done→archived for all done.
- `clear_queue()`: delete staged|failed episodes (and their processed_articles rows for failed only, not done/archived).
- `stats()`: total_minutes (sum est_minutes for staged + duration_sec/60 for done), counts by status, current drive_id.