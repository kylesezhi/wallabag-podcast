---
plan name: add-article-url
plan description: Stage article by URL or ID
plan status: done
---

## Idea
Allow the user to add a single Wallabag article to the Staged queue of the current podcast by pasting the article's Wallabag view URL (e.g. http://192.168.42.223:8000/view/2793) or a bare entry ID into a new input in the drive card (below "Add N Random Articles").

Confirmed decisions:
- Input: view URL (any host, /view/{id} path, tolerate trailing slash/query) OR bare numeric ID
- Archived (read) articles are stageable — explicit intent overrides add_random's unread-only rule
- EXCLUDE_TAGS filtering is bypassed for explicit adds
- Duplicates are rejected with an error flash distinguishing: "already in this podcast" / "already staged in another podcast" / "already generated"
- UI: text input + "Add Article" button (btn-secondary btn-block) in the drive card; classic form POST + 303 redirect flash (consistent with add-random); success flash includes the article title; empty-queue hint text updated to mention paste-by-URL

Mechanics: parse ref -> entry_id; fetch via WallabagClient.get_entry (ArticleFull carries all staging fields: id, title, domain_name, url, reading_time, language); dedupe against episodes rows (any status) and processed_articles; insert_staged_episode with podcast_id of the current podcast. Also fix get_entry's missing status-code check (a 404 currently parses into a garbage ArticleFull(id=0)).

## Implementation
- app/wallabag.py: add status-code check to get_entry (app/wallabag.py:299) — resp.status_code >= 300 raises WallabagError; 404 message 'Article {id} not found in Wallabag'. Also improves generate_all failure accuracy.
- app/pipeline.py: add _parse_wallabag_ref(ref) -> int — strip; bare digits -> int; else urlparse with path matching ^/view/(\d+)/?$ (host ignored, trailing slash/query tolerated); else ValueError('Not a Wallabag article URL — paste the /view/… URL or an entry ID').
- app/pipeline.py: add async add_article(ref, wallabag_client, settings=None, podcast_id=None) -> str (returns title) — parse ref, await get_entry, dedupe (episode row in same podcast -> 'already in this podcast'; episode row in another podcast -> 'already staged in another podcast'; else processed_articles hit -> 'already generated'; all ValueError), then insert_staged_episode with podcast_id.
- app/main.py: new POST /podcast/{guid}/queue/add-article modeled on podcast_add_random (app/main.py:482) — 404 unknown guid; read form field 'url'; ValueError/WallabagError -> error flash redirect; success -> redirect with message 'Added "{title}"'.
- templates/index.html: in drive card after the add-random form (templates/index.html:57-59) add text input (placeholder 'Paste Wallabag article URL or ID…', autocomplete=off) + btn btn-secondary btn-block 'Add Article'; update empty-queue hint (templates/index.html:139) to mention pasting a URL.
- static/css/style.css: style the input per DESIGN.md field spec (#181818 bg, 1px solid #2d2d2d border, 0.25rem radius, #8a6b47 focus border, #6e6a64 placeholder) reusing .filter-search tokens as reference; full-width input stacked above the block button.
- tests/test_wallabag.py: get_entry 404/500 raise WallabagError (httpx mock handler convention).
- tests/test_pipeline.py: add_article tests — URL parsing variants (host+port, trailing slash, query string, bare ID, whitespace), invalid/empty refs raise, success stages with podcast_id and returns title, archived article allowed, excluded tag bypassed, three duplicate cases raise with correct messages.
- tests/test_web.py: route tests mirroring add-random (tests/test_web.py:677-757) — success flash with title, ValueError flash, WallabagError flash, empty field flash, unknown guid 404.
- README.md: document the Add Article control alongside Add Random.
- Verify: run pytest and ruff check (per mise.toml/pyproject.toml).

## Required Specs
<!-- SPECS_START -->
- config-and-env
- data-model
- architecture-and-stack
- backend-logging
<!-- SPECS_END -->