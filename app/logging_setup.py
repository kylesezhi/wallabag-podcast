"""Application logging configuration.

Configured once at startup from :func:`app.main.lifespan` via
:func:`configure_logging`. Wires a console handler and a rotating file handler
under ``DATA_DIR/logs/wallabag-podcast.log`` — the data dir is volume-mounted
in docker-compose (``./data:/app/data``), so rotated logs survive container
restarts. Uses ``dictConfig``-free direct handler installation so uvicorn's
own loggers (configured earlier during server startup) keep working.

Idempotent: re-entry (tests, ``--reload``) replaces the previously installed
handlers instead of stacking duplicates, and closes the old file handles.
"""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from .config import Settings

_LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
_LOG_FILE_NAME = "wallabag-podcast.log"
_MAX_BYTES = 5 * 1024 * 1024  # 5 MB per file
_BACKUP_COUNT = 3  # keeps .log + .1/.2/.3 (~20 MB cap)

# Marker stamped on handlers this module owns, so re-entry removes only its
# own handlers and leaves anything uvicorn/third-parties installed untouched.
_CONFIGURED_ATTR = "_wp_logging_configured"


def configure_logging(settings: Settings) -> None:
    """Configure root + app logging for the running process.

    Console output always goes to stderr; a rotating file captures the same
    records under ``settings.DATA_DIR/logs/``. ``LOG_LEVEL`` sets the
    verbosity for the root, ``app``, and ``uvicorn`` loggers;
    ``uvicorn.access`` is quieted to WARNING (request logs are noisy at INFO).
    """
    log_dir = Path(settings.DATA_DIR) / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / _LOG_FILE_NAME
    level = _parse_level(settings.LOG_LEVEL)

    root = logging.getLogger()
    # Idempotency: close+drop only the handlers we previously installed so
    # re-entry (tests, uvicorn --reload) does not stack duplicates or leak file
    # handles. Handlers installed by uvicorn or other libraries are preserved.
    for handler in list(root.handlers):
        if getattr(handler, _CONFIGURED_ATTR, False):
            root.removeHandler(handler)
            handler.close()

    formatter = logging.Formatter(_LOG_FORMAT, datefmt=_DATE_FORMAT)

    console = logging.StreamHandler()
    console.setFormatter(formatter)
    setattr(console, _CONFIGURED_ATTR, True)

    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=_MAX_BYTES,
        backupCount=_BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    setattr(file_handler, _CONFIGURED_ATTR, True)

    root.addHandler(console)
    root.addHandler(file_handler)
    root.setLevel(level)

    # The app logger propagates to root (no handler of its own). uvicorn's own
    # loggers carry their own handlers (propagate=False); we only tune level.
    for name in ("app", "uvicorn", "uvicorn.error"):
        logging.getLogger(name).setLevel(level)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)


def _parse_level(value: str) -> int:
    """Resolve a level name (case-insensitive) to its numeric logging level.

    Falls back to INFO when the name is not a recognized stdlib level.
    """
    level = getattr(logging, str(value).upper(), None)
    return level if isinstance(level, int) else logging.INFO
