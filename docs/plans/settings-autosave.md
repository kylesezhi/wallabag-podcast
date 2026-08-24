---
plan name: settings-autosave
plan description: Autosave settings, drop button
plan status: done
---

## Idea
Remove the Save button from templates/settings.html and make settings save automatically whenever a value changes (articles-per-drive range, voice select). Add an inline status indicator where the button was: "Saving…" while in flight, "Saved ✓" on success, red error text on failure. Implementation: new IIFE in static/js/app.js listening for `change` events inside `.settings-form`, POSTing the form via fetch with `Accept: application/json`. Backend: extend POST /settings handler in app/main.py to return JSON ({ok:true} or 400 {error}) when the request wants JSON; keep existing redirect+flash behavior for normal form posts so no-JS fallback and existing tests stay green. Disabled Automation controls are excluded automatically (disabled inputs don't fire change/submit). Small CSS additions for the status line. Update/extend tests: assert settings page renders without a submit button; add test for JSON-mode save success + validation error.

## Implementation
- [object Object]
- [object Object]
- [object Object]
- [object Object]
- [object Object]
- [object Object]

## Required Specs
<!-- SPECS_START -->
- architecture-and-stack
- config-and-env
- data-model
<!-- SPECS_END -->