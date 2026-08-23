---
plan name: feed-ui-tweaks
plan description: Clipboard icon and author cleanup
plan status: done
---

## Idea
Two UI changes plus config cleanup for wallabag-podcast:

1) Add a small inline-SVG copy icon right after the Subscribe URL (`<code>{{ base_url }}/feed.xml</code>`) in templates/index.html (podcast card). Clicking copies the URL to the clipboard with visible feedback (icon swaps to a checkmark ~1.5s). Must work on plain HTTP LAN origins where `navigator.clipboard` is unavailable → include `document.execCommand('copy')` fallback.

2) Remove the superfluous "Author Kyle" row from the UI everywhere: templates/index.html AND templates/settings.html. Since it leaves the UI entirely, also remove the FEED_AUTHOR setting end-to-end: app/config.py field, app/rss.py channel author lines (+docstring mention), feed_author keys from both render_context dicts in app/main.py, templates/settings.html hint text, .env.example, README.md table row, docs/specs/config-and-env.md, and the stale FEED_AUTHOR line in the local .env.

3) Update tests/test_rss.py (drop the itunes:author assertion) and run the full pytest suite to verify nothing else depends on FEED_AUTHOR.

## Implementation
- templates/index.html: delete the <dt>Author</dt><dd>{{ feed_author }}</dd> pair; wrap the Subscribe URL dd content with an inline two-squares SVG copy button (<button class="btn-copy" data-copy-url="{{ base_url }}/feed.xml">) carrying both a copy icon SVG and a checkmark SVG
- static/js/app.js: append an IIFE that binds click handlers to .btn-copy — read data-copy-url, try navigator.clipboard.writeText, fall back to a hidden-textarea + document.execCommand('copy') for non-secure (HTTP LAN) contexts, then toggle a .copied class for ~1.5s so the checkmark shows before reverting
- static/css/style.css: add .btn-copy styles near the Podcast info section — borderless inline-flex icon button in tan (--tan) matching existing icon styling, hover darker, plus .copied rules hiding the copy glyph and showing the green checkmark
- Remove FEED_AUTHOR end-to-end: app/config.py (delete FEED_AUTHOR field), app/rss.py (delete fg.author/itunes_author lines, fix docstring), app/main.py (drop feed_author from both template contexts at ~211/~265), .env.example, README.md env table row, docs/specs/config-and-env.md, local .env stale line
- templates/settings.html: delete the Author dt/dd pair and update the section-hint to reference only FEED_TITLE and BASE_URL
- tests/test_rss.py: remove the itunes:author assertion (keep the surrounding channel-metadata comment block); run uv run pytest (or .venv/bin/pytest) and confirm the whole suite passes

## Required Specs
<!-- SPECS_START -->
- config-and-env
- data-model
- architecture-and-stack
<!-- SPECS_END -->