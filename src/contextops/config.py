"""Environment-backed application settings."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    app_env: str = os.getenv("APP_ENV", "development")
    log_level: str = os.getenv("LOG_LEVEL", "INFO")
    database_path: str = os.getenv("DATABASE_PATH", "data/contextops.db")
    ai_provider: str = os.getenv("AI_PROVIDER", "deterministic")


settings = Settings()
