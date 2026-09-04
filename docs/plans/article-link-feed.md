---
plan name: article-link-feed
plan description: Add Wallabag link to RSS descriptions
plan status: done
---

## Idea
Add a link to the original Wallabag article in each podcast episode's show notes (the RSS description field). The Wallabag view URL follows the pattern `{WALLABAG_URL}/view/{wallabag_id}`. The `get_feed_episodes` query already returns `wallabag_id`, and `Settings.WALLABAG_URL` holds the base URL. The change is to modify `build_feed` in `app/rss.py` to include this link in the episode description, and update the RSS tests accordingly.

## Implementation
- Modify `build_feed` in `app/rss.py` to include the Wallabag view URL in the episode description. Change the description line to append a clickable link to the Wallabag article using `settings.WALLABAG_URL` and `episode['wallabag_id']`.
- Update RSS tests in `tests/test_rss.py` — add or modify a test that asserts the description contains the Wallabag view URL for the episode.
- Check for any other tests that assert on the old description format (e.g. in `tests/test_rss.py`).
- Run the full test suite to verify all changes pass.
- Verify the generated RSS XML by inspecting a test feed output to confirm the link renders correctly.

## Required Specs
<!-- SPECS_START -->
<!-- SPECS_END -->