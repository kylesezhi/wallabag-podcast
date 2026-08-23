---
plan name: podcast-cover-art
plan description: Cover art for feed episodes
plan status: done
---

## Idea
Use reference/cover.png as the cover art for the podcast RSS feed: set it as the channel (show) cover and as the cover for every episode (item). The cover is served as a static asset at {BASE_URL}/static/cover.png and referenced via feedgen's iTunes podcast extension plus the legacy RSS <image> element. Confirmed decisions: (1) Feed scope = channel-level <itunes:image> AND per-item <itunes:image>, plus channel-level legacy RSS <image> element for validator compatibility. (2) Serving = copy reference/cover.png to static/cover.png; served by the existing FastAPI /static mount; Dockerfile already bundles static/ so no Docker change. (3) URL = fixed/derived from the existing BASE_URL setting as f"{BASE_URL}/static/cover.png" — no new env var, no DB change, no UI change. (4) UI = feed-only; favicon and brand styling untouched. reference/cover.png is retained as the design source; static/cover.png is the canonical served copy. Non-goals: no env var, no UI/favicon work, no Wallabag mutation. Note: the cover is 1254x1254 PNG (~1.7MB), which is below Apple Podcasts' 1400px directory minimum but is valid RSS and fine for self-hosted LAN consumption; this is documented in the README. Architecture: only app/rss.py build_feed() changes (compute cover_url, call fg.podcast.itunes_image(cover_url) + fg.image(cover_url, title=FEED_TITLE, link=BASE_URL) at the channel, and entry.podcast.itunes_image(cover_url) per item) plus the committed static/cover.png asset. Dockerfile/config.py/db.py unchanged.

## Implementation
- Copy reference/cover.png to static/cover.png and commit the asset so the existing /static mount serves it at {BASE_URL}/static/cover.png (no new route; Dockerfile already bundles static/).
- Modify app/rss.py build_feed(): compute cover_url = f"{settings.BASE_URL}/static/cover.png"; at the channel call fg.podcast.itunes_image(cover_url) and fg.image(cover_url, title=settings.FEED_TITLE, link=settings.BASE_URL); inside the episode loop call entry.podcast.itunes_image(cover_url) so every <item> carries the cover.
- Extend tests/test_rss.py: assert the channel <itunes:image> href equals {BASE_URL}/static/cover.png, the channel <image><url> equals the same, and every <item> has an <itunes:image> with that href; also assert the channel-level cover is present in the empty-feed case (no episodes).
- Add a web test (tests/test_web.py or tests/test_rss.py) asserting GET /static/cover.png returns 200 with content-type image/png (proves the asset is actually served).
- Update README.md: document that the podcast uses a single cover image for the show and all episodes, that it lives at static/cover.png (copied from reference/cover.png, the design source), served at {BASE_URL}/static/cover.png, and note the 1254x1254 size is below Apple's 1400px directory minimum but fine for self-hosted LAN use.
- Run `uv run pytest -q` and confirm the full suite is green; sanity-check a real /feed.xml payload by hand to confirm <itunes:image> appears at channel and item level with the correct absolute URL.

## Required Specs
<!-- SPECS_START -->
- architecture-and-stack
- config-and-env
- data-model
<!-- SPECS_END -->