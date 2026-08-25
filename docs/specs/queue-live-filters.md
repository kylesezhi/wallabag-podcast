# Spec: queue-live-filters

Scope: feature

# Queue Live Filters

## Requirement
Users can narrow the episode queue list on the home page without any server round-trip, using two client-side controls:

1. **Title search** — free-text input that live-filters episodes by case-insensitive substring match against the episode title.
2. **State pills** — toggle buttons for each episode status: Staged, Generating, Done, Failed. Pills toggle independently so any combination of states may be shown simultaneously.

## Behavior
- A row is visible only when it passes BOTH filters: title matches AND its status pill is active.
- Default state: all four pills active and search empty → full list visible.
- Pills are filled (badge-colored) when active, outlined when inactive; colors reuse the status-badge palette per state.
- When one or more rows are hidden but the underlying queue is non-empty, show an inline "no episodes match your filters" hint if zero rows remain — visually distinct from the true empty-queue card.
- Filter controls are rendered only when the queue has episodes.
- Selections intentionally reset on page reload (the page self-reloads when generation completes); no persistence required.
- Filtering must not interfere with live generation polling: hidden rows' progress bars still update by element id.

## Implementation surface
Client-side only: `templates/index.html` (filter bar markup + hint), `static/js/app.js` (visibility toggling), `static/css/style.css` (pill/input styling). No backend or route changes; rows carry `status-{{ ep.status }}` classes already used as filter hooks.

## Rationale
The queue is small-to-medium (tens of items) and fully server-rendered; a fetch-based server filter adds latency and complexity with no benefit at this scale. DOM hide/show is instant, works offline, and composes with existing vanilla-JS patterns in this codebase.