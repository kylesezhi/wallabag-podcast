# Spec: dynamic-cover-names

Scope: feature

# Dynamic Cover Names — Feature Spec

Each podcast's cover art carries its own name, rendered at request time onto the shared base artwork. The base asset stays pristine; text is a per-podcast overlay served from a dedicated route.

## Problem / Context

Podcasts are created with random, immutable two-word names (e.g. "Quantum Quokka") via `app/naming.py` / `db.create_podcast`. All feeds previously shared one static cover (`{BASE_URL}/static/cover.png`), so no podcast's art identified it. A single baked-in name would be wrong for every other podcast; the only correct design is per-podcast rendering.

## Decisions (interview-confirmed)

### Scope & route
- **Dynamic per-podcast covers**: `GET /podcast/{guid}/cover.png` composites the podcast's name onto the base; feeds reference this URL. `static/cover.png` is never modified — it remains the compositing base and stays fetchable at `/static/cover.png`.
- Unknown guid → 404 (reuse `_get_podcast_or_404`).
- UI is **feed-only**: no hub/template changes; covers appear only in RSS/podcast apps.

### Font
- **PixelOperator8-Bold.ttf** (8px-design bold cut — chunkiest 8-bit look), by Jayvee Enaguas (HarvettFox96), **CC0 1.0 public domain**, ~18KB.
- Committed at `app/assets/fonts/PixelOperator8-Bold.ttf`; loaded via `importlib.resources` (same pattern as `assets/gap_1s.mp3`); `assets/fonts/*.ttf` added to pyproject package-data.

### Rendering (Pillow)
- **Pillow** added as a main dependency (`pillow>=10`); wheel-based, so the `python:3.12-slim` Docker image needs no system packages.
- Text: **white fill + black outline**, `stroke_width = max(2, size // 16)`.
- Name rendered **exactly as stored** (Title Case — string-identical to the feed channel title).
- Placement: **horizontally centered, single line**, text block bottom ≈ **72px** above the image bottom (inside the near-black bottom band, verified ≈RGB(15,17,19) across the bottom ~190px of the 1254×1254 base).
- Auto-fit: start at **72px** (multiple of the 8px design grid), shrink in steps of **8px** until text width ≤ **~88% of canvas width (≈1104px)**; floor **16px**. Worst-case 25-char names (e.g. "Shopping Cart Gas Station") stay on one line.

### Serving & caching
- **In-memory process cache** `{guid: PNG bytes}`; first request renders off the event loop (`asyncio.to_thread`), repeats serve cached bytes. No disk cache, no `delete_podcast` file cleanup; cache entry invalidated on podcast delete.
- `Cache-Control: public, max-age=86400` (not `immutable`, so future style changes propagate within a day).
- **Render failure → graceful fallback**: log the exception, serve the pristine `static/cover.png` bytes (the overlay is cosmetic; feeds keep working).

### RSS integration
- `build_feed(podcast)`: `cover_url = f"{BASE_URL}/podcast/{guid}/cover.png"` for the channel `itunes:image`, the legacy `<image>` element, and every episode's `itunes:image`.
- `build_feed(podcast=None)` (tests-only legacy path) keeps `{BASE_URL}/static/cover.png`.

## Non-goals
- No rename feature, no DB schema change (names are already immutable).
- No disk cache of rendered covers; no Dockerfile change (font rides in `app/`, Pillow via uv lock).
- No web-UI display of covers; no favicon/brand changes.
- No image resizing — 1254×1254 stays (Apple's 1400px directory minimum remains out of scope for LAN self-hosting).

## Testing
- Renderer unit tests: white pixels present in the expected bottom band; auto-fit shrinks a long name; deterministic bytes for a fixed name.
- Route tests: 200 + `image/png`; second request returns identical bytes (cache hit); 404 unknown guid; induced render failure serves the base cover bytes.
- `tests/test_rss.py`: per-podcast cover-URL assertions updated to `{BASE_URL}/podcast/{guid}/cover.png`; static-cover-served test retained.
- README "Cover art" section rewritten for per-podcast covers + Pixel Operator CC0 attribution.