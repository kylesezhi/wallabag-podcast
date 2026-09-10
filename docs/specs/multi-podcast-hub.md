# Spec: multi-podcast-hub

Scope: feature

# Multi-Podcast Hub

## Scope
Replaces the single-podcast model with a multi-podcast hub: any number of
randomly named podcasts, each with its own queue, its own per-GUID RSS feed,
and a shared Wallabag article pool. GUIDs are the sole URL identity; names are
display-only but still unique.

## Data Model

- New `podcasts` table: `id` INTEGER PK AUTOINCREMENT, `guid` TEXT UNIQUE NOT
  NULL (8-char lowercase hex, `uuid4().hex[:8]`), `name` TEXT UNIQUE NOT NULL
  (Title Case), `created_at` TEXT. `get_podcasts` orders by `id ASC`, so header
  tabs append new podcasts to the right.
- `episodes.podcast_id` INTEGER (nullable; index `idx_episodes_podcast_id`, no
  FK clause) scopes every episode row to its owning podcast.
- The no-repeat invariant is GLOBAL and cross-podcast: `episodes.wallabag_id`
  stays `UNIQUE` across ALL podcasts, and `processed_articles` is a single
  global dedupe index. The candidate pool for `add_random` is
  `processed_articles ∪ any episode row`, shared by every podcast — an article
  can only ever exist in one podcast.
- Settings stay global (`articles_per_drive`, `voice`, `automation_*`).

## Name Generator (`app/naming.py`)

Pure module (stdlib `random` only) with hardcoded word pools transcribed from
`reference/words.md`, stored lowercase except Places (Title Case):
Adjectives (61), Animals (73), Objects (73, incl. multiword like
"garden hose"), Science (46), Places (40).

`generate_name(existing_names, rng=None)` rolls a weighted pattern:

- 60% Adjective + Animal
- 25% Adjective + Object
- 15% (Objects ∪ Science) + Place (e.g. "Paradox Harbor", "Pickle Observatory")

Output is Title Case; a collision with `existing_names` regenerates; a
`RuntimeError` is raised after 200 attempts. All randomness flows through the
injected `random.Random` (`rng`) so the module is fully deterministic under
test.

## Fresh-Start Migration (`init_db`)

When the `podcasts` table did NOT exist before `init_db` runs (an upgrade from
ANY pre-multi-podcast database OR a brand-new database):

- drop/recreate `episodes` with `podcast_id`,
- wipe `processed_articles`,
- best-effort unlink `DATA_DIR/audio/*.mp3` and `*.part`,
- create ONE randomly named podcast,
- log one INFO line.

User settings SURVIVE (the `INSERT OR IGNORE` seed does not overwrite them).

> **Warning:** booting this version against an existing data dir wipes episodes
> and generated audio. Settings are preserved and one podcast is created
> automatically.

When the `podcasts` table already exists, nothing is wiped and no podcast is
auto-created — deleting the last podcast leaves a legitimate zero-podcast state
(the hub shows a "New Podcast" CTA).

## Route Map

| Route | Behavior |
| --- | --- |
| `GET /` | 303 to the newest podcast hub (flash query passthrough); zero podcasts → empty hub with New Podcast CTA |
| `GET /podcast/{guid}` | Hub page: scoped episodes + stats + podcast/podcasts context; unknown guid → 404 |
| `GET /podcast/{guid}/feed.xml` | `build_feed(podcast)` — per-podcast RSS; unknown guid → 404 |
| `POST /podcasts/create` | Create a randomly named podcast; redirect to its hub |
| `POST /podcast/{guid}/delete` | JSON-or-redirect; cancels the podcast's own run first, then `pipeline.delete_podcast` |
| `POST /podcast/{guid}/queue/add-random` | Stage N random articles into that podcast (global candidate pool) |
| `POST /podcast/{guid}/queue/generate` | Scoped failed→staged sweep, then `_run_generation(app, podcast_id)`; global generating guard |
| `POST /podcast/{guid}/queue/clear` | Clear that podcast's staged/failed episodes |
| `GET /queue/status?podcast={guid}` | JSON: generating, generating_podcast_id, scoped stats + episodes, podcasts nav array; missing/unknown guid → newest podcast; zero podcasts → minimal payload |
| `POST /queue/{id}/delete`, `POST /queue/{id}/archive` | Global by episode id; stop-first when mid-run; redirect to the OWNING podcast's hub |
| `GET /episode/{id}/delete` | Confirm page (RSS delete links point here) |
| `POST /queue/stop` | Global stop; redirects to the run's podcast |
| `GET /feed.xml` | REMOVED — 404, clean break |
| `GET /audio/{id}.mp3` | Unchanged (range-aware) |

## Generation Model

- One global generation asyncio task at a time (`app.state.generation_task` +
  `app.state.generating`).
- `app.state.generating_podcast_id` records which podcast the run belongs to.
- A run processes ONLY the triggering podcast's staged episodes; `drive_id`
  stays global per run.
- The busy state (`generating`) shows on ALL podcasts (tab status dot, hero
  button spinner, progress card).
- Episode actions targeting the in-flight episode trigger stop-first (cancel);
  the row flips to `failed` and is then deletable.

## Podcast Deletion

`pipeline.delete_podcast(podcast_id)`:

- deletes the podcast row and all of its episode rows,
- removes the `processed_articles` rows for its wallabag_ids — the articles
  become re-pickable,
- best-effort unlinks every episode mp3 AND `{id}.mp3.part` (mid-run safety),
- logs one INFO line and returns `{"name", "guid", "episode_count"}`,
- raises `ValueError` on an unknown id.

The route cancels the active generation task first when the run belongs to
this podcast (`app.state.generating_podcast_id == podcast["id"]`).

## RSS

- `build_feed(podcast=None, settings=None)` — with a podcast record: channel
  title = podcast name, id/self link = `{BASE_URL}/podcast/{guid}/feed.xml`,
  alternate link = hub page, shared `static/cover.png`, generic description.
- Per-episode logic unchanged (guids, enclosures, delete links).
- `podcast=None` still emits the legacy global feed, but NO route serves it
  anymore — `GET /feed.xml` is gone.
- `FEED_TITLE` is now the app BRAND (header/footer/settings page), never an
  RSS title.

## UI Structure

Hand-written CSS (Nocturne Editorial tokens; no Tailwind), desktop + mobile
responsive per `reference/stitch_dynamic_podcast_ui_theme/`:

- **Header:** brand icon + horizontal-scroll podcast tab pills (amber dot when
  active, name, count badge) + New Podcast button + gear.
- **Hero card:** "Active Podcast" label, name, ping-dot status
  (Generating… / Ready to generate / Ready to listen / No episodes yet),
  Total Length + Episodes, Generate Audio + "Add N Random Articles".
- **Progress card** while generating (Stop Generating).
- **Episodes section:** "Episodes for {name}" + "N queued • N ready • N failed",
  Clear Staged, title filter + status pills, per-episode rows (progress bar,
  error, inline Archive/Delete, confirm modal, Wallabag source links).
- **Other Managed Podcasts grid:** name, "N episodes • length • status",
  Switch / Delete.
- **Podcast Subscription aside:** Feed Name, GUID feed URL + copy button,
  Delete Podcast + "Removes feed and all N episodes".
- **Waveform card** kept; **zero-podcast empty CTA**; footer
  "FEED_TITLE — Multi-Podcast Engine".

## JS Contract (`static/js/app.js`)

- Polls `/queue/status?podcast={guid}` while generating (guid resolved from
  `data-podcast-guid`); reloads when `generating` flips false; updates chunk
  progress bars.
- Confirm modal honors `data-redirect` (podcast delete navigates to `/`).
- Active-tab `scrollIntoView` on load.
- Title filter + status pills scope to the active episode list; settings
  autosave and feed-URL copy unchanged.

## Testing

Covered in the suite:

- naming: pool contents/casing, weight distribution within tolerance (seeded
  rng), collision retry, 200-attempt safety valve;
- cross-podcast no-repeat: `add_random` never stages an article already known
  to another podcast;
- scoped generation: `generate_all` only processes the target podcast's staged
  episodes;
- e2e journey: create → generate → delete a podcast through the web routes;
- realistic upgrade test: pre-multi-podcast schema → `init_db` wipes legacy
  data but keeps user settings.