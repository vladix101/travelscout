"""Application settings.

The root `.env` (one level above `backend/`) is loaded explicitly so the backend behaves
the same regardless of the working directory it is started from.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = BACKEND_DIR.parent
ENV_FILE = REPO_ROOT / ".env"

DEFAULT_OPENAI_MODEL = "gpt-4o-mini"


def load_env_file() -> bool:
    """Load the root .env into the process environment (does not override real env vars)."""
    if ENV_FILE.is_file():
        load_dotenv(ENV_FILE, override=False)
        return True
    return False


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=None, extra="ignore", case_sensitive=False)

    # --- OpenAI -------------------------------------------------------------------------
    openai_api_key: SecretStr | None = None
    openai_model: str = DEFAULT_OPENAI_MODEL
    openai_timeout_seconds: float = 60.0
    openai_max_retries: int = 2

    # --- Optional LangSmith tracing (off by default) --------------------------------------
    langsmith_tracing: bool = False
    langsmith_api_key: SecretStr | None = None
    langsmith_project: str = "travelscout"

    # --- Live providers (opt-in; see docs/PROVIDERS.md) -----------------------------------
    wizz_live_enabled: bool = False
    booking_scraper_enabled: bool = False
    booking_headless: bool = False
    osm_enabled: bool = True
    fx_live_enabled: bool = True
    contact_email: str = Field(default="", description="Optional contact sent in the OSM User-Agent.")

    # --- Workflow budget -----------------------------------------------------------------
    max_shortlist: int = Field(default=5, ge=1, le=15)
    max_properties_per_destination: int = Field(default=5, ge=1, le=12)
    max_job_seconds: int = Field(default=420, ge=30, le=1800)
    provider_timeout_seconds: float = 30.0
    provider_concurrency: int = Field(default=4, ge=1, le=10)
    booking_concurrency: int = Field(default=2, ge=1, le=4)
    cache_ttl_minutes: int = 180

    # --- Storage ---------------------------------------------------------------------------
    database_path: Path = BACKEND_DIR / "data" / "travelscout.sqlite3"
    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]

    @property
    def llm_available(self) -> bool:
        key = self.openai_api_key.get_secret_value() if self.openai_api_key else ""
        return bool(key.strip()) and not key.startswith("sk-your")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    load_env_file()
    settings = Settings()
    _configure_tracing(settings)
    return settings


def _configure_tracing(settings: Settings) -> None:
    """LangSmith tracing is opt-in. Force it off unless explicitly enabled with a key."""
    enabled = settings.langsmith_tracing and settings.langsmith_api_key is not None
    os.environ["LANGSMITH_TRACING"] = "true" if enabled else "false"
    os.environ["LANGCHAIN_TRACING_V2"] = "true" if enabled else "false"
    if enabled:
        os.environ.setdefault("LANGSMITH_PROJECT", settings.langsmith_project)
