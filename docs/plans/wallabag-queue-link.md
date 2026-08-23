---
plan name: wallabag-queue-link
plan description: Link queue titles to Wallabag view
plan status: done
---

## Idea
Make each article's title in the queue listing a clickable hyperlink that opens the article inside the user's Wallabag instance at {WALLABAG_URL}/view/{wallabag_id}, in a new tab. The data layer already stores wallabag_id; only minor plumbing is needed to surface it in the queue query and template context, then render the title as an anchor with appropriate styling. No schema changes, no new routes, no new dependencies.

## Implementation
- app/db.py: Add wallabag_id to the SELECT statement and returned dict in get_queue_episodes; update the docstring key list to include wallabag_id.
- app/main.py: Add 'wallabag_url': settings.WALLABAG_URL to the home route's template context dict (matches existing settings-page convention).
- templates/index.html: Replace the <p class='queue-item-title'> element with an <a class='queue-item-title'> linking to {{ wallabag_url.rstrip('/') }}/view/{{ ep.wallabag_id }} with target='_blank' and rel='noopener noreferrer'.
- static/css/style.css: Add a rule for a.queue-item-title setting color (theme-consistent, e.g. var(--green)), text-decoration:none, and underline on hover, so the link does not render with browser-default blue/underline.
- tests/test_web.py: Add a test (test_home_article_links_to_wallabag) asserting the rendered home page contains href ending in /view/{id} with target='_blank' and rel='noopener noreferrer' for each staged article; reuse the existing _insert_staged helper.
- Run full test suite (pytest) to confirm no regressions and the new test passes.

## Required Specs
<!-- SPECS_START -->
- config-and-env
- data-model
- architecture-and-stack
<!-- SPECS_END -->