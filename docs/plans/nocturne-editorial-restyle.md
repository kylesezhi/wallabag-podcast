---
plan name: nocturne-editorial-restyle
plan description: Nocturne Editorial dark UI re-skin
plan status: active
---

## Idea
Restyle the entire web UI (dashboard, settings, delete-confirmation modal, confirm-delete fallback page) to the "Nocturne Editorial" dark theme from reference/stitch_dynamic_podcast_ui_theme/ with ZERO functional changes. Approach: full rewrite of static/css/style.css around Nocturne tokens (ground #131313, cards #1c1b1b, hairline #2e2c2b borders, amber #c8955a/#f4bc7d accents) keeping ALL existing class names/selectors alive; minimal template changes (base.html gets Google Fonts CDN — Newsreader/Literata/Hanken Grotesk/JetBrains Mono — plus Material Symbols Outlined icons and a literary footer; index.html restructured to editorial queue rows with hairline dividers, icon-button row actions, stat labels, decorative waveform sidebar card; settings.html + confirm-delete.html restyled). HARD CONSTRAINTS: app.js and all Python code untouched; every JS hook preserved (#queue-title-filter, .filter-pill[data-status]+.active, .queue-list .queue-item[data-status], .queue-item-title, #queue-no-match, #ep-progress-{id}+.queue-item-progress-fill, #generation-progress[data-generating], #progress-done, #progress-total, #delete-modal{,-body,-confirm,-cancel}, form[data-confirm-message]/data-confirm-label, form.settings-form, #save-status+.saved/.save-error, .btn-copy[data-copy-url]+.copied+.icon-copy/.icon-check); every test-asserted string preserved verbatim (class="btn btn-primary btn-block", class="spinner", status-badge-generating, data-confirm-label="Clear", "staged and failed episodes... Done episodes are kept", title="4 of 12 chunks synthesized", id="progress-done">2<, "Stop Generating", action="/queue/stop", "Coming soon", "remove its audio file" done-episodes-only, no btn-block on settings page). Root DESIGN.md replaced with the Nocturne Editorial spec from reference/stitch_dynamic_podcast_ui_theme/nocturne_editorial/DESIGN.md. Regression gate: uv run pytest -q fully green, unchanged. User decisions: Google Fonts CDN, Material Symbols CDN, icon buttons for row actions, replace DESIGN.md, include waveform card.

## Implementation
- Task 1 — Foundation & shared chrome: replace root DESIGN.md with Nocturne Editorial spec; rewrite static/css/style.css core (design tokens in :root, base elements, .site-header with Material Symbols, flash messages, footer colophon, .btn variants incl. exact .btn.btn-primary.btn-block markup support, .spinner, inputs/selects/range slider, .card, modal) and update templates/base.html (Google Fonts + Material Symbols links, icon header, footer). Acceptance: all four pages render with new theme chrome; uv run pytest -q green; JS hooks in base intact.
- Task 2 — Dashboard HTML: restructure templates/index.html — drive card with stat labels + status dot, progress card, queue as hairline-divided editorial rows with Material Symbols icon buttons for archive/delete wrapped in existing forms/attrs, filter bar (search + pills), sidebar podcast card + decorative waveform card, delete modal restyle. Acceptance: all test_web.py home assertions pass verbatim; all app.js hooks intact.
- Task 3 — Dashboard CSS & responsive: add page CSS for drive card, stats, queue rows, badges, filter pills, progress bar, sidebar cards incl. waveform animation; home responsive rules per desktop/mobile mockups. Acceptance: dashboard matches reference mockups on desktop + mobile widths.
- Task 4 — Settings + confirm-delete pages: restyle templates/settings.html (cards with mono uppercase labels, custom amber range slider, styled select, disabled toggle, connected/not-connected badges, coming-soon chip, save-status line) and templates/confirm-delete.html; add CSS. Acceptance: settings tests pass (incl. no btn-block on settings), confirm-delete fallback page themed.
- Task 5 — Final verification: run uv run pytest -q; grep-verify every preserved hook/test string; check responsive breakpoints, focus-visible states, prefers-reduced-motion (waveform + spinner), color contrast spot checks; cross-check all four pages against reference mockups; fix any gaps found.

## Required Specs
<!-- SPECS_START -->
- config-and-env
- data-model
- architecture-and-stack
- backend-logging
<!-- SPECS_END -->