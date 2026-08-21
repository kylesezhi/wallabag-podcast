"""Application configuration loaded from .env / environment variables.

Secrets (Wallabag credentials) live ONLY here — never in the DB or UI.
UI-tunable settings (articles_per_drive, voice, automation_*) are persisted
in the SQLite `settings` table and override these defaults at runtime.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Annotated

from pydantic import BeforeValidator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


def _split_tags(value: object) -> object:
    """Split a comma-separated string into a list of trimmed tags."""
    if isinstance(value, str):
        return [tag.strip() for tag in value.split(",") if tag.strip()]
    return value


class Settings(BaseSettings):
    """All environment variables for the app (see docs/specs/config-and-env.md)."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    # --- Wallabag (OAuth password grant) ---
    WALLABAG_URL: str = "https://app.wallabag.it"
    WALLABAG_CLIENT_ID: str
    WALLABAG_CLIENT_SECRET: str
    WALLABAG_USERNAME: str
    WALLABAG_PASSWORD: str

    # --- Kokoro-FastAPI ---
    KOKORO_BASE_URL: str = "http://localhost:8880"
    KOKORO_DEFAULT_VOICE: str = "af_heart"
    KOKORO_SPEED: float = 1.0
    KOKORO_RESPONSE_FORMAT: str = "mp3"

    # --- App ---
    BASE_URL: str = "http://localhost:8000"
    HOST: str = "127.0.0.1"
    PORT: int = 8000
    DATA_DIR: Path = Path("./data")
    # NoDecode stops pydantic-settings from JSON-parsing the env value; the
    # BeforeValidator below splits the comma-separated string into a list.
    EXCLUDE_TAGS: Annotated[list[str], NoDecode, BeforeValidator(_split_tags)] = []
    MIN_TEXT_CHARS: int = 200
    MAX_FETCH_PAGES: int = 50
    FEED_TITLE: str = "My Wallabag Podcast"
    FEED_AUTHOR: str = "Kyle"


# Resolve forward references introduced by `from __future__ import annotations`
# so settings sources can correctly read env values (e.g. EXCLUDE_TAGS).
Settings.model_rebuild()


@lru_cache
def get_settings() -> Settings:
    """Return a cached Settings instance (parses .env once per process)."""
    return Settings()
