---
plan name: queue-live-filters
plan description: Client-side queue list filtering
plan status: done
---

## Idea
Add a live filter bar to the Queue section of the home page (templates/index.html) so the user can narrow the episode list without a page round-trip. Two controls, both purely client-side:

1. A text input that live-filters episodes by title (case-insensitive substring match on the .queue-item-title text).
2. Four toggle pills — Staged, Generating, Done, Failed — matching the existing status-badge color palette. Pills toggle independently so any combination of states can be shown. Default state: all four active (everything visible). Clicking a pill deactivates that state's rows; re-activating restores them.

Implementation is entirely in templates/index.html, static/js/app.js, and static/css/style.css. No backend/route changes: each li.queue-item already carries status-{{ ep.status }} class hooks for filtering.

Behavior details:
- Filtering combines title match AND state match (row hidden unless both pass).
- When rows are filtered out but the queue itself isn't empty, show an inline "no episodes match" hint instead of the empty-queue card.
- Filters reset on page reload (the page self-reloads when generation completes); no persistence.
- Filter bar only rendered server-side when there are episodes ({% if episodes %} guard), so empty queues don't show dead controls.
- Keep existing polling JS untouched; hiding rows must not break the ep-progress-* bar updates (they look up by id, which still works even if the generating row is hidden).

## Implementation
- Add filter-bar markup to templates/index.html between .queue-header and the queue list: a search input (type=search, placeholder 'Filter by title…', aria-label) plus four pill buttons labeled Staged/Generating/Done/Failed with data-status attributes; render inside the {% if episodes %} block so it disappears with an empty queue.
- Add a 'no matches' hint element after the queue list in index.html (hidden by default) with text like 'No episodes match your filters.', distinct from the existing empty-queue card.
- Add a new IIFE in static/js/app.js: read all .queue-item rows once, wire 'input' event on the title field (debounce ~120ms optional) and 'click' toggles on the pills, then apply visibility via the hidden attribute per row based on (title substring match) AND (status pill active).
- In the same IIFE, count visible rows after each apply() and toggle the no-matches hint; also add a double-click/'clear' affordance? — no: keep scope minimal, pills default all-active and input empties via native search clear.
- Add CSS in static/css/style.css: .queue-filter layout (flex row wrapping for mobile), .filter-search input styling consistent with settings inputs, and pill styling reusing status-badge colors — inactive = transparent bg + colored border/text, active = filled badge colors (.filter-pill[data-status=staged].active etc.), plus hover/focus-visible states.
- Verify manually with just-run dev server: type to filter titles, toggle each pill and combos, confirm generating progress bar still updates while hidden, confirm reload resets filters, run pytest to confirm no template-breaking regressions.

## Required Specs
<!-- SPECS_START -->
- architecture-and-stack
- data-model
- config-and-env
- queue-live-filters
<!-- SPECS_END -->