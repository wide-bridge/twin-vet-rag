"""Application settings.

Precedence (highest first): process env > project `.env` > external ENV_FILE > defaults.
Secrets are never logged or returned by the API.
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr, field_validator
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
    # LLM (docs/04 §5 model table). Empty env values fall back to these defaults.
    llm_model: str = "gpt-5-mini"
    llm_classifier_model: str = "gpt-5-nano"
    llm_timeout_seconds: float = Field(default=60.0, gt=0)
    enable_source_excerpts: bool = False

    # Retrieval (calibrated on eval_dev: in-domain top-1 p05 0.565, off-topic probes max 0.438)
    retrieval_top_k: int = Field(default=6, ge=1, le=20)
    retrieval_min_score: float = Field(default=0.40, ge=-1.0, le=1.0)
    history_turns: int = Field(default=3, ge=0, le=10)  # previous Q/A pairs given to the models

    # Sessions, requests, runs (docs/03 §3, docs/04 §3)
    session_ttl_days: int = Field(default=7, ge=1)
    cookie_secure: bool = True  # browsers accept Secure cookies on http://localhost
    allowed_origins: str = "http://localhost:5173,http://127.0.0.1:5173,http://localhost:8000,http://127.0.0.1:8000"
    question_max_chars: int = Field(default=1000, ge=10)
    questions_per_minute: int = Field(default=6, ge=1)
    run_timeout_seconds: int = Field(default=120, ge=10)
    worker_threads: int = Field(default=2, ge=0)  # 0 = no background worker (tests drive runs directly)

    life_stage_puppy_max_months: int = Field(default=12, ge=1)
    life_stage_senior_min_months: int = Field(default=84, ge=1)


    @field_validator("llm_model", "llm_classifier_model", mode="before")
    @classmethod
    def _empty_means_default(cls, v, info):
        return cls.model_fields[info.field_name].default if v in (None, "") else v

    @property
    def origins(self) -> set[str]:
        return {o.strip().rstrip("/") for o in self.allowed_origins.split(",") if o.strip()}


@lru_cache
def get_settings() -> Settings:
    return Settings()
