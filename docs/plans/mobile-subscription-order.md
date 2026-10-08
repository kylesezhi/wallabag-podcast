---
plan name: mobile-subscription-order
plan description: Reorder mobile panel layout
plan status: done
---

## Idea
On mobile (<1024px), the home page renders .home-grid as a single column, so the entire .info-column aside (Podcast Subscription card + decorative Waveform card) lands after the whole .drive-column — putting Podcast Subscription at the very bottom, below the episode list and "Other Managed Podcasts". User wants the Podcast Subscription panel directly below the Active Podcast hero card but above the "Episodes for {name}" episode list, specifically after the transient "Generating…" progress card (decided: order = Active Podcast → Generating… → Podcast Subscription → Episodes list), with the Waveform decorative card staying at the very bottom (decided).

Constraint: the two panels live in separate wrapper columns (.drive-column section and .info-column aside), so `order` on .home-grid children alone cannot move a card between columns. Solution: CSS-only, mobile-scoped `display: contents` on .drive-column/.info-column so the cards become direct grid items of .home-grid (grid honors `order`), plus explicit order values; desktop (≥1024px) is fully restored inside the existing @media (min-width: 1024px) block. No HTML/JS/Python changes. Note: on mobile the cards previously spaced only by their own margins now also get .home-grid's 1.25rem gap (e.g. progress-card's 1rem margin-top and queue-header's 2rem margin-top stack on top of the gap) — slightly roomier but consistent rhythm; acceptable.

## Implementation
- Edit static/css/style.css base (mobile-first) section after the .info-column flex rule (~line 227) and before the 1024px media query: add a rule making .drive-column and .info-column `display: contents` so their cards become direct grid items of .home-grid (keep the existing `.drive-column, .info-column { min-width: 0 }` and `.info-column` flex rules for desktop use).
- In the same base section, add explicit mobile order values: .drive-card {order: 1}; .progress-card {order: 2}; .podcast-card {order: 3} (Podcast Subscription — right above the episodes list); .queue-header, .queue-filter, .queue-list, .queue-no-match, .queue-empty {order: 4} (same value keeps their DOM relative order); .other-podcasts {order: 5}; .waveform-card {order: 6} (stays last).
- Inside the existing @media (min-width: 1024px) block (~line 229), add desktop resets: .drive-column { display: block; } and .info-column { display: flex; flex-direction: column; gap: 1.5rem; } so the desktop two-column sidebar layout is byte-for-byte unchanged. No `order` resets are needed: order is ignored in the block-layout .drive-column, and 3 < 6 preserves podcast-card before waveform-card inside the flex .info-column.
- Regression gate: run `uv run pytest -q` — tests assert template strings only (no CSS assertions), so the suite must stay fully green; also confirm no changes to templates/index.html, static/js/app.js, or app/ code (git diff should show static/css/style.css only).
- Manual verification in a browser: at a mobile viewport (~375-412px) confirm visual order Active Podcast → (Generating… when active) → Podcast Subscription → Episodes for {name} list → Other Managed Podcasts → Synthesizer Engine waveform card, with no horizontal overflow (fix-mobile-overflow minmax(0,1fr) guard still intact); at ≥1024px confirm the sidebar still shows Podcast Subscription + waveform on the right and nothing moved.
- Eyeball mobile vertical spacing between the reordered cards (grid gap 1.25rem now stacks with existing margins like .progress-card's 1rem margin-top and .queue-header's 2rem margin-top); tighten only if a gap looks visibly off.

## Required Specs
<!-- SPECS_START -->
- config-and-env
- data-model
- architecture-and-stack
- backend-logging
<!-- SPECS_END -->