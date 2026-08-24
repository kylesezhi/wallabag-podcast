---
plan name: humanize-duration-display
plan description: Friendly time strings in UI
plan status: done
---

## Idea
Replace raw minute counts in the web UI with humanized duration text. Today templates/index.html shows `stats.total_minutes` labeled "minutes" for the drive total, and `xx min` per queue episode (from duration_sec//60 when done, else est_minutes). Add a Jinja custom filter `human_duration(minutes)` registered on templates.env in app/main.py that renders full-word durations: drops zero units, handles singular/plural, e.g. "45 minutes", "2 hours, 5 minutes", "1 day, 3 hours", falling back to "0 minutes" for empty input. Apply the filter in both places in index.html. Podcast RSS feed duration handling (app/rss.py itunes_duration) stays untouched. Unit tests cover the filter edge cases.

## Implementation
- Add `_human_duration(minutes: int) -> str` helper in app/main.py near the templates setup (divmod into days/hours/minutes, skip zero units, pluralize, return '0 minutes' when all zero) and register it via `templates.env.filters["human_duration"] = _human_duration`.
- Update the drive-total block in templates/index.html (lines ~13-17): render `{{ stats.total_minutes | human_duration }}` in stat-value and remove or repurpose the static "minutes" stat-label span.
- Update the per-episode duration lines in templates/index.html (lines ~62-69): wrap both `(ep.duration_sec // 60)` and `ep.est_minutes` with the `| human_duration` filter, keeping the clock emoji and existing conditional.
- Check static/css/style.css `.stat`, `.stat-value`, `.stat-label` rules still lay out correctly with variable-length text like "1 day, 3 hours"; adjust only if wrapping/alignment breaks.
- Add unit tests in tests/test_web.py for the filter: 45->'45 minutes', 60->'1 hour', 61->'1 hour, 1 minute', 125->'2 hours, 5 minutes', 1440->'1 day', 1501->'1 day, 1 hour, 1 minute', 0/None->'0 minutes'.
- Run the test suite (pytest) and any lint/typecheck commands configured in the repo; verify the home page renders correctly.

## Required Specs
<!-- SPECS_START -->
- architecture-and-stack
- config-and-env
- data-model
<!-- SPECS_END -->