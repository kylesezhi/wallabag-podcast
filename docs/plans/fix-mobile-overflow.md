---
plan name: fix-mobile-overflow
plan description: Panels fit phone viewport
plan status: done
---

## Idea
On a real Pixel 9 (~412px CSS viewport) the home page cards render ~475px wide and overflow horizontally (right-hand clipping of Generate Audio / Add Random / Stop Generating buttons), even though desktop devtools mobile mode looked fine. Root cause in static/css/style.css: `.home-grid`/`.settings-grid` use bare `1fr` tracks (implicit `minmax(auto, 1fr)` minimum), so any item's min-content width — long unbreakable queue title/error text, or flex rows (`.queue-item`, `.drive-stats`) whose children default to `min-width: auto` — widens the shared column beyond the viewport; every card in the column then renders at that inflated width. Fix: use `minmax(0, 1fr)` / `minmax(0, 2fr) minmax(0, 1fr)` tracks, add `min-width: 0` to grid/flex children, allow long words to wrap (`overflow-wrap: anywhere` on queue title/error), and let `.drive-stats`/`.queue-item-meta` wrap. Additionally tighten mobile spacing/typography (user opted in): smaller `.content`/card padding and `drive-title` size on narrow screens via a small-screen media query. CSS-only change; no template/Python changes needed.

## Implementation
- Edit static/css/style.css: change `.home-grid, .settings-grid` mobile rule to `grid-template-columns: minmax(0, 1fr)`, and the @media (min-width: 900px) rules to `minmax(0, 2fr) minmax(0, 1fr)` (home) and `minmax(0, 1fr) minmax(0, 1fr)` (settings) so tracks can shrink below content min-width.
- Add shrink/wrap safety for children: `min-width: 0` on `.drive-column`, `.info-column`, and `.queue-item-body`; `overflow-wrap: anywhere` on `.queue-item-title` and `.queue-item-error`; `flex-wrap: wrap` on `.drive-stats` and `.queue-item-meta`.
- Add a narrow-screen media query (e.g. @media (max-width: 480px)) tightening mobile density: `.content` padding ~0.9rem, `.card` padding ~1rem, `.drive-title` ~1.5rem, `.stat-value` ~1.35rem, `.site-header` padding ~0.6rem 1rem.
- Verify with `just run` + Chrome DevTools device mode at 412x915 (Pixel 9): confirm document.scrollWidth <= innerWidth with a queued article having a long URL-like title, and confirm the >=900px two-column desktop layout is unchanged.
- Optionally sanity-check `just test` passes (web tests render templates) and review final diff for no unrelated style changes.

## Required Specs
<!-- SPECS_START -->
- config-and-env
- data-model
- architecture-and-stack
<!-- SPECS_END -->