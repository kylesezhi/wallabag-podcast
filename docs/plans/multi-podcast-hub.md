---
plan name: multi-podcast-hub
plan description: Randomly named podcast feeds
plan status: done
---

## Idea
Replace the single-podcast app with a multi-podcast hub. Resolved decisions from interview (2026-09-09):

IDENTITY & DATA MODEL
- New `podcasts` table: id INTEGER PK, guid TEXT UNIQUE (uuid4().hex[:8], 8-char lowercase hex, uniqueness-checked on create), name TEXT UNIQUE (Title Case, regenerate on collision), created_at.
- `episodes.podcast_id` FK column. GUIDs are the sole URL identity; names are display-only but still unique.
- Global no-repeat invariant preserved: `episodes.wallabag_id UNIQUE` + `processed_articles` stay global across all podcasts (an article can only ever be in one podcast once).

NAMING (app/naming.py, hardcoded word lists from reference/words.md)
- 60% Adjective+Animal, 25% Adjective+Object, 15% Noun+Noun where the first noun is drawn from Objects ∪ Science words and the second is always a Place (e.g. "Paradox Harbor", "Pickle Observatory"). Title Case output.

ROUTES
- GET / → 302 redirect to newest podcast; when zero podcasts exist, empty hub with "New Podcast" CTA.
- GET /podcast/{guid} — hub page with that podcast active.
- GET /podcast/{guid}/feed.xml — per-podcast RSS: title = podcast.name, id/self link = {BASE_URL}/podcast/{guid}/feed.xml, shared static/cover.png, generic description; episode entries/guids/audio URLs unchanged.
- POST /podcasts/create — instant create with random name (no prompt), redirect to new podcast.
- POST /podcast/{guid}/delete — confirm modal ("Removes feed and all N episodes") → delete episode rows, unlink mp3s (best-effort), remove processed_articles rows so articles become re-pickable.
- POST /podcast/{guid}/queue/add-random, /podcast/{guid}/queue/generate, /podcast/{guid}/queue/clear — scoped actions.
- GET /queue/status becomes podcast-aware (active podcast episodes + stats + generating flag + nav counts).
- Episode actions POST /queue/{id}/delete and /queue/{id}/archive stay global by episode id; redirect back to owning podcast page. /audio/{id}.mp3 unchanged.
- GET /feed.xml → 404 (removed, clean break).
- FEED_TITLE stays as the app brand (header/footer/settings), not a feed title.

BEHAVIOR
- One global generation asyncio task at a time (app.state.generating), processing only the triggering podcast's staged episodes; busy state shown on all podcasts. drive_id per run unchanged.
- Failed-episode sweep on Generate scoped to that podcast only.
- add_random unchanged: global candidate pool minus known articles (processed + any episode row), random.sample n, stages into the active podcast.
- Settings (articles_per_drive, voice, automation_*) stay global.

MIGRATION — fresh start
- In init_db: when the `podcasts` table did not exist before this call (upgrade from old schema OR brand-new DB), wipe all episodes + processed_articles rows, delete DATA_DIR/audio/*.mp3 (+ .part), create one randomly-named podcast.
- Zero podcasts allowed thereafter (deleting the last one shows the empty-CTA hub); auto-create only happens on the upgrade/new-DB path, never on normal boots.

UI (hand-written CSS, same Nocturne Editorial tokens as reference; no Tailwind)
- Hub layout per reference/stitch_dynamic_podcast_ui_theme: header with brand icon + horizontal-scroll podcast tabs (name + episode-count badge, active tab highlighted with amber dot) + "New Podcast" button + settings icon.
- Active-podcast hero card: "Active Podcast" label, name, status line with ping dot (Ready to listen / Generating… / Ready to generate / No episodes yet), Total Length + Episodes stats (humanized), Generate Audio (spinner/busy when any run active) + "Add N Random Articles" buttons.
- Episodes section: "Episodes for {name}" + "X queued • Y ready" meta (failed appended when > 0), Clear Staged, title filter + status pills, episode list with existing per-episode behaviors (progress bar, error, inline AJAX Archive/Delete, confirm modal, Wallabag source links) — all scoped to the active podcast.
- "Other Managed Podcasts" grid: name, "N episodes • length • status", Switch + Delete buttons.
- Subscription aside: Feed Name, feed URL in code + copy button, BASE_URL hint, Delete Podcast button + "Removes feed and all N episodes".
- Waveform card kept. No edition number. Mobile = responsive variant of the same template (podcast pill row per mobile reference). Footer: FEED_TITLE — Multi-Podcast Engine style colophon.

Repo conventions: uv run pytest -q (just test); one docs/specs/<feature>.md per feature; no comments in code unless asked.

## Implementation
- Schema + migration + repo layer: add podcasts table (id, guid, name, created_at) and episodes.podcast_id to app/db.py; implement fresh-start migration in init_db (wipe episodes/processed_articles/audio when podcasts table is newly created, then create one random podcast); add podcast CRUD + per-podcast stats/episode-list query functions (get_podcasts with counts/minutes/status, get_podcast_by_guid, create_podcast, delete_podcast).
- Name generator: create app/naming.py with hardcoded word pools from reference/words.md (adjectives, animals, objects, science words, places) and weighted generation — 60% Adjective+Animal, 25% Adjective+Object, 15% (Object|Science)+Place — Title Case, uniqueness-checked against existing names; unit tests for pools, weights, casing, and collision retry.
- Pipeline scoping: thread podcast through add_random (stage into target podcast), generate_all (process only that podcast's staged episodes; per-run drive_id unchanged), reset_failed_to_staged scoped to podcast, and per-podcast stats(); delete_podcast orchestration (episodes + audio unlink + processed_articles cleanup); update test_pipeline.py.
- RSS per podcast: build_feed(podcast) with podcast name as channel title, {BASE_URL}/podcast/{guid}/feed.xml as id/self link, shared static cover; shared enclosure/guid/delete-link logic preserved; update test_rss.py.
- Routes: rewrite app/main.py routes to the new map — / redirect to newest (empty-CTA when zero), /podcast/{guid} hub, /podcast/{guid}/feed.xml, /podcasts/create, /podcast/{guid}/delete + /queue scoped actions, podcast-aware /queue/status, remove /feed.xml (404), episode delete/archive redirect to owning podcast; update test_web.py.
- Templates + CSS: restructure templates/ (hub index, podcast tabs header, hero, episodes, Other Managed Podcasts, subscription aside, empty state, podcast-delete confirm modal reusing modal system, footer) and extend static/css/style.css with the hub layout per the desktop+mobile references; keep FEED_TITLE as app brand.
- JS updates: static/js/app.js — podcast-aware polling, tab switching links, feed-URL copy, podcast delete confirm wiring, filters/pills scoped to active episode list.
- Tests: full suite pass under uv run pytest -q — update test_web/test_rss/test_pipeline/test_scaffold for new schema/routes; add tests for naming, podcast CRUD, per-podcast feeds, cross-podcast article non-repetition, fresh-start migration, and last-podcast deletion empty state.
- Docs: write docs/specs/multi-podcast-hub.md capturing the design (data model, naming weights, routes, migration, UI) per repo convention; update docs/specs/data-model.md and architecture-and-stack.md where they describe the single feed/queue.

## Required Specs
<!-- SPECS_START -->
- config-and-env
- data-model
- architecture-and-stack
- backend-logging
<!-- SPECS_END -->