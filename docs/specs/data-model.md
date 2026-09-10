# Spec: data-model

Scope: repo

# Data Model &amp; Queue State Machine

SQLite DB at `data/podcast.db`. Single user. The database holds any number of
podcasts; each podcast has its own queue and its own RSS feed (see
`docs/specs/multi-podcast-hub.md`).

## Tables

### settings (key/value)
- `key` TEXT PRIMARY KEY
- `value` TEXT
- `updated_at` TEXT (ISO)

Keys: `articles_per_drive` (int), `voice` (str), `automation_enabled` (bool, false in v1), `automation_time` (str e.g. "07:00").
Defaults seeded from .env on first run; updated via the settings UI.

### podcasts
- `id` INTEGER PRIMARY KEY AUTOINCREMENT
- `guid` TEXT UNIQUE NOT NULL   # 8-char lowercase hex (uuid4().hex[:8]); the URL identity
- `name` TEXT UNIQUE NOT NULL   # Title Case, display-only; regenerated on collision
- `created_at` TEXT

`get_podcasts` orders by `id ASC` so header tabs append new podcasts to the right.

### episodes
- `id` INTEGER PRIMARY KEY AUTOINCREMENT
- `wallabag_id` INTEGER UNIQUE   # one episode per Wallabag article — GLOBALLY unique across ALL podcasts (cross-podcast no-repeat)
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
- `podcast_id` INTEGER           # owning podcast (nullable; index idx_episodes_podcast_id, no FK clause)

### processed_articles (global dedupe index, shared by all podcasts)
- `wallabag_id` INTEGER PRIMARY KEY
- `episode_id` INTEGER           # nullable
- `processed_at` TEXT

A `wallabag_id` is added here ONLY on successful generation. The index is
GLOBAL across podcasts: an article generated in one podcast can never be
re-picked into another. Deleting a `done`
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
The state machine is identical inside every podcast — the multi-podcast hub
changes episode *scoping*, not episode *semantics*. Per-episode ops
(`delete_item`, `archive_item`) stay global by episode id; `staged` /
`generating` / `done` / `failed` behave exactly as before.
- **staged:** candidate fetched, no audio yet. Deletable via the per-item Delete button (removes the row; article stays unread, re-pickable by add_random). Archivable via the per-item Archive button (marks article read in Wallabag; episode stays queued).
- **generating:** being synthesized; a task cancellation (Stop) marks it `failed` ("Cancelled by user"). Deletable via the Delete button, which always renders; during an active run the delete route triggers Stop first (`task.cancel()`, marking the episode `failed` for removal), while an orphan (no active run) is deleted directly. Archivable via the Archive button at any time (no conflict with the generation loop).
- **done:** has audio; appears in the RSS feed. Deletable via the per-item Delete button — this unlinks the mp3, removes the processed_articles row, and the article stays unread (re-pickable by add_random). Archivable via the per-item Archive button — marks the article read in Wallabag; the episode, mp3, and RSS entry all stay.
- **failed:** generation error or user cancellation ("Cancelled by user"); retryable — clicking Generate Audio sweeps all failed episodes back to `staged` (error cleared) so they join the next run. Deletable via the Delete button. Archivable via the Archive button.

## Queue ops (pipeline layer)
Queue ops take an optional `podcast_id` to scope to one podcast; the per-episode
ops (`delete_item`, `archive_item`, `stop_generation`) stay global by episode
id and redirect to the owning podcast's hub.
- `add_random(n, wallabag_client, settings=None, podcast_id=None)`: enumerate unread (Wallabag `archive=0, detail=metadata`) excluding EXCLUDE_TAGS (client-side tag filter) and not in processed_articles; pick n random; insert as `staged` with `est_minutes=reading_time`. The candidate pool is GLOBAL across podcasts (processed_articles ∪ any episode row) — an article can only ever be staged into one podcast; when `podcast_id` is given the staged articles belong to that podcast. Idempotent on wallabag_id (skip if already staged/done). Articles deleted via `delete_item` stay unread and become re-pickable (dedupe row removed). Articles archived via `archive_item` are marked read and excluded from enumeration.
- `delete_item(id)`: DELETE the episode row locally (sync, no Wallabag call, no network I/O). For `done` episodes also unlink the mp3 at `audio_path` (best-effort) and remove the `processed_articles` row so the article becomes re-pickable by `add_random`. Raises `ValueError` if the episode is missing or `archived` (non-deletable). The article stays unread in Wallabag.
- `archive_item(id)`: mark the episode's article as read in Wallabag (`PATCH /api/entries/{wallabag_id}.json`, `archive=1`). Does NOT delete anything locally — the episode row, mp3, and processed_articles row are all left intact. On `WallabagError` the error propagates and nothing local changed. Raises `ValueError` if the episode is missing or `archived` (non-archivable).
- `stop_generation()`: cancel the active generation task (`app.state.generation_task.cancel()`); the in-flight episode is marked `failed` ("Cancelled by user") by `generate_all`'s CancelledError handler; remaining staged episodes stay `staged`. (Main.py-level op.)
- `generate (POST /podcast/{guid}/queue/generate)`: FIRST reset that podcast's `failed` episodes to `staged` (`reset_failed_to_staged`, error cleared, scoped to the podcast), then start the run if anything is staged — a failed-only queue therefore generates instead of erroring; an empty queue still shows "No staged articles to generate". One global generation task at a time (`app.state.generating`); a run in progress is rejected with "A generation run is already in progress".
- `generate_all(wallabag_client, kokoro_client, settings=None, podcast_id=None)`: for the target podcast's staged (or all staged when `podcast_id` is None) → status=generating; clean text; split into bounded chunks (`KOKORO_MAX_CHUNK_CHARS`, sentence boundaries); synthesize each chunk with ONE automatic retry on KokoroError, appending bytes to `data/audio/{id}.mp3.part` and persisting `progress_done`/`progress_total` around every chunk; on completion atomically rename the part file to `{id}.mp3`; duration = sum of per-chunk measurements (fallback: est_minutes when any chunk is unparseable); status=done + insert processed_articles. Each run — scoped or global — draws one global `drive_id`. A failed or cancelled episode has its `.part` file removed (best-effort) — no partial audio is ever served or kept. Continue past per-article failures (mark failed, keep going). A task cancellation aborts the run: the in-flight episode is marked `failed` ("Cancelled by user") and the run halts (remaining staged stay staged). An episode removed mid-run while still `staged` is skipped when its turn comes: no TTS call, no audio file, no processed_articles row — it counts as neither done nor failed (`summary.total` is decremented).
- `clear_queue(podcast_id=None)`: delete staged|failed episodes (does not touch done episodes or any processed_articles rows). When `podcast_id` is given, only that podcast's staged|failed episodes are deleted.
- `stats(podcast_id=None)`: total_minutes (sum est_minutes for staged + duration_sec/60 for done), counts by status, current drive_id. When `podcast_id` is given the statistics cover only that podcast's episodes.
- `delete_podcast(podcast_id)`: delete the podcast row and all of its episode rows; remove the `processed_articles` rows for its wallabag_ids (the articles become re-pickable by `add_random`); best-effort unlink every generated mp3 plus any in-progress `{episode_id}.mp3.part` (mid-run safety); log one INFO line; return `{"name", "guid", "episode_count"}`. Raises `ValueError` on an unknown id.