"""Application settings.

Precedence (highest first): process env > project `.env` > external ENV_FILE > defaults.
Secrets are never logged or returned by the API.
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_EXTERNAL_ENV = r"D:\PyProject\env_keys\.env"


def _env_files() -> tuple[str, ...]:
    # pydantic-settings: later files override earlier ones.
    external = os.environ.get("ENV_FILE", DEFAULT_EXTERNAL_ENV)
    files = []
    if external and Path(external).is_file():
        files.append(external)
    project_env = PROJECT_ROOT / ".env"
    if project_env.is_file():
        files.append(str(project_env))
    return tuple(files)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=_env_files(), env_file_encoding="utf-8", extra="ignore")

    openai_api_key: SecretStr | None = None
    database_url: str = "postgresql+psycopg://twinvet:twinvet_local@127.0.0.1:55433/twinvet"
    aihub_data_dir: Path = Path(r"D:\ai_hub_data")
    data_output_dir: Path = PROJECT_ROOT / "data_output"
    embedding_model: str = "text-embedding-3-small"
    llm_model: str | None = None
    enable_source_excerpts: bool = False

    life_stage_puppy_max_months: int = Field(default=12, ge=1)
    life_stage_senior_min_months: int = Field(default=84, ge=1)


@lru_cache
def get_settings() -> Settings:
    return Settings()
