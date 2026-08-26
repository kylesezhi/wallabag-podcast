# Spec: backend-logging

Scope: repo

# Spec: backend-logging

Scope: repo

# Backend Logging

## Configuration
- `LOG_LEVEL` env var (default `INFO`) controls verbosity for the `app` logger hierarchy and uvicorn loggers.
- Logging is configured once at startup via `configure_logging(settings)`, called from `lifespan()` AFTER `init_db()` so DATA_DIR exists. Uses `dictConfig` with `disable_existing_loggers=False` to preserve uvicorn's own loggers.
- `get_settings()` is the single source for `LOG_LEVEL` and `DATA_DIR`; tests swap `DATA_DIR` to a tmp path so no real data is polluted.

## Handlers
- **Console** (`StreamHandler`): always on, for `docker logs` / terminal.
- **File** (`RotatingFileHandler`): writes to `DATA_DIR/logs/wallabag-podcast.log`, `maxBytes=5MB`, `backupCount=3` (keeps `.log` + `.1`/`.2`/`.3`, ~20MB cap). DATA_DIR is volume-mounted (`./data:/app/data`) so rotated files survive container restarts.
- Format: `%(asctime)s %(levelname)s %(name)s: %(message)s`.

## Logger levels
- Root + `app` + `uvicorn`/`uvicorn.error` → `LOG_LEVEL`.
- `uvicorn.access` → `WARNING` (request logs are noisy at INFO).
- `configure_logging` is idempotent: re-entry (tests, --reload) must not duplicate handlers.

## Failure recording contract
- `episodes.error` keeps a SHORT, user-facing reason (terse for the queue UI):
  - `KokoroError`/`WallabagError` → `str(exc)`.
  - Unexpected `Exception` → `f"Unexpected: {type(exc).__name__}: {exc}"` trimmed to ~200 chars.
  - `SkipArticle` → `f"Skipped: {exc}"`; cancellation → `"Cancelled by user"`.
- The FULL traceback is written to the log file via `logger.exception(...)` (unexpected) or `logger.error(..., exc_info=True)` (Kokoro/Wallabag) — never stored in the DB.
- Failure logs include chunk position read from the persisted `progress_done`/`progress_total` row ("failed at chunk X/Y").

## Lifecycle logging (INFO)
- Run start: `"Generation run started: N staged episodes"`.
- Per-episode start: `"Generating episode %s (wallabag=%s, %d chunks)"`.
- Per-episode done: `"Episode %s done: %ds audio, %d chunks"`.
- Run end: existing `"Generation finished: %s"` summary log.
- `_run_generation` (main.py) keeps `logger.exception("Generation run crashed")` — now formatted + persisted.