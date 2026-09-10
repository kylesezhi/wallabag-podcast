---
plan name: dynamic-cover-names
plan description: Render show title on artwork
plan status: done
---

## Idea
Per-podcast cover art: render each podcast's (random, immutable) name as white Pixel Operator text onto the existing 1254x1254 static/cover.png, served dynamically. Interview-confirmed decisions:

(1) SCOPE — Dynamic per-podcast, not a static bake-in: every feed gets its own correctly-named cover. New route GET /podcast/{guid}/cover.png composites the name onto the pristine base; static/cover.png itself stays unmodified as the compositing base and remains fetchable at /static/cover.png.

(2) FONT — PixelOperator8-Bold.ttf (8px-design bold cut, chunkiest 8-bit look) by Jayvee Enaguas (HarvettFox96), CC0 1.0 public domain, ~18KB, committed at app/assets/fonts/ (bundled like gap_1s.mp3 via importlib.resources; add assets/fonts/*.ttf to pyproject package-data).

(3) RENDERING — Pillow (new main dependency; wheel-based so the python:3.12-slim Docker image needs no system packages). Style: white fill + black outline via stroke_width = max(2, size // 16); name rendered exactly as stored (Title Case); horizontally centered; text block bottom ~72px above the image bottom (inside the near-black bottom band — verified RGB ~15,17,19 across bottom ~190px); single line; auto-fit: start at 72px (multiple of the 8px design grid), shrink in steps of 8px until width <= ~88% of canvas (≈1104px), floor 16px (covers worst-case 25-char names like "Shopping Cart Gas Station").

(4) SERVING — In-memory process cache {guid: PNG bytes}; first request renders off the event loop (asyncio.to_thread), repeats serve cached bytes. No disk cache, no delete_podcast file cleanup (cache entry invalidated on delete). Cache-Control: public, max-age=86400. On render failure: log exception and serve the pristine static/cover.png bytes as graceful fallback (feed keeps working; overlay is cosmetic).

(5) RSS — app/rss.py build_feed: when podcast is given, cover_url = {BASE_URL}/podcast/{guid}/cover.png for channel itunes:image + legacy <image> + per-episode itunes:image. The podcast=None legacy path (tests only) keeps the static URL.

(6) UI — Feed-only, matching the original podcast-cover-art decision; no hub/template changes.

(7) TESTS — Renderer unit tests (white pixels in bottom band, auto-fit shrink, deterministic output), route tests (200 image/png, cached identical bytes, 404 unknown guid, fallback on induced failure), and tests/test_rss.py cover-URL assertion updates. README Cover art section updated with CC0 attribution.

## Implementation
- Add pillow>=10 to pyproject.toml main dependencies and uv sync (updates uv.lock); commit PixelOperator8-Bold.ttf (~18KB, CC0) to app/assets/fonts/ and add 'assets/fonts/*.ttf' to [tool.setuptools.package-data]
- Create app/covers.py: render_cover_png(name) -> bytes loading the base via the static dir and font via importlib.resources; white fill, black stroke_width=max(2, size//16), bottom-center with ~72px bottom margin, single line, auto-fit starting at 72px shrinking by 8px until width <= 88% of 1254px (floor 16px); plus a module-level {guid: bytes} cache with an invalidate(guid) helper
- Add GET /podcast/{guid}/cover.png route in app/main.py: 404 for unknown guid; render on first request via asyncio.to_thread, cache bytes, respond with media_type image/png and Cache-Control: public, max-age=86400; on render exception log it and fall back to serving pristine static/cover.png bytes
- Update app/rss.py build_feed(): when podcast is given set cover_url = f'{settings.BASE_URL}/podcast/{podcast["guid"]}/cover.png' (channel itunes:image, legacy <image>, per-episode itunes:image); podcast=None path keeps {BASE_URL}/static/cover.png
- Invalidate the covers cache entry (covers.invalidate(guid)) in the podcast delete route so deleted podcasts don't linger in memory
- Add tests: unit tests for render_cover_png (white pixels present in the bottom band; auto-fit shrinks a long name; deterministic bytes for a fixed name); route tests (200 + image/png, second request returns identical bytes, 404 on unknown guid, fallback serves the base cover when rendering is made to fail); update tests/test_rss.py cover-URL assertions to the per-podcast URL
- Update README.md Cover art section (per-podcast covers with the name overlay, Pixel Operator CC0 attribution) and run uv run pytest -q plus ruff to verify everything passes

## Required Specs
<!-- SPECS_START -->
- config-and-env
- data-model
- architecture-and-stack
- backend-logging
- dynamic-cover-names
<!-- SPECS_END -->