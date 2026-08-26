---
plan name: robust-error-logging
plan description: Backend generation failure traces
plan status: done
---

## Idea
Backend generation failures leave no trace: there is NO logging configuration in the codebase (logger.info calls are silently dropped; logger.warning/exception hit bare stderr via Python's lastResort with no timestamps, lost on Docker restart), and the episodes.error DB column stores only the literal "Unexpected error" (or str(exc)) with no traceback, no cause chain, and no chunk position. Goal: configure structured, persistent logging (rotating file under the volume-mounted DATA_DIR + console), persist short user-facing reasons in episodes.error while writing full tracebacks to the log file, and add INFO lifecycle logs (run start/end, per-episode start/done) so successful and failed runs are both diagnosable. See linked backend-logging spec for the reusable contract.

## Implementation
- Add LOG_LEVEL: str = "INFO" to Settings in app/config.py and to .env.example; document it in docs/specs/config-and-env.md.
- Create app/logging_setup.py with configure_logging(settings): dictConfig (disable_existing_loggers=False) wiring root -> StreamHandler + RotatingFileHandler(DATA_DIR/logs/wallabag-podcast.log, maxBytes=5MB, backupCount=3); format '%(asctime)s %(levelname)s %(name)s: %(message)s'; uvicorn.access->WARNING, root/app/uvicorn->LOG_LEVEL; idempotent re-entry guard.
- Call configure_logging(get_settings()) in lifespan() (app/main.py) AFTER init_db(get_db_path()) and BEFORE yielding.
- Enrich generate_all failure handling (app/pipeline.py): unexpected 'except Exception as exc' -> set_episode_failed(conn, episode_id, f"Unexpected: {type(exc).__name__}: {exc}"[:200]) with logger.exception; KokoroError/WallabagError -> keep str(exc) in DB but switch logger.warning to logger.error(..., exc_info=True); read persisted progress_done/progress_total to log 'failed at chunk X/Y' on every failure branch.
- Add INFO lifecycle logs in generate_all: run start 'Generation run started: N staged episodes'; per-episode start 'Generating episode %s (wallabag=%s, %d chunks)'; per-episode done 'Episode %s done: %ds audio, %d chunks'; keep existing run-end summary log.
- Add tests (matching tests/test_pipeline.py + tests/test_web.py style): configure_logging creates DATA_DIR/logs/wallabag-podcast.log and respects LOG_LEVEL; a pipeline failure stores type(exc).__name__ in episodes.error and writes a traceback line to the log file; an INFO lifecycle line is emitted on a successful episode.
- Note the log file location (DATA_DIR/logs/wallabag-podcast.log, rotated 5MB x3) in docs/specs/architecture-and-stack.md.

## Required Specs
<!-- SPECS_START -->
- backend-logging
- config-and-env
- data-model
- architecture-and-stack
<!-- SPECS_END -->