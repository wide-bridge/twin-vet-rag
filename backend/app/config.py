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
    worker_idle_poll_seconds: int = Field(default=0, ge=0)  # 0 = no DB queries while idle (woken on submit)
    # worker: in-process worker threads answer queued runs (Docker / local).
    # request: no background threads (serverless, e.g. Vercel); the browser calls POST /runs/{id}/execute
    #          and that request runs the pipeline. Defaults to request when VERCEL is set.
    run_mode: str = Field(default_factory=lambda: "request" if os.environ.get("VERCEL") else "worker",
                          pattern="^(worker|request)$")

    life_stage_puppy_max_months: int = Field(default=12, ge=1)
    life_stage_senior_min_months: int = Field(default=84, ge=1)

    # Settings (P08): dog profile kept for N days after the last use (docs/03 §3, 2026-11-12)
    profile_ttl_days: int = Field(default=90, ge=1)
    # 주치동물병원 search: Kakao Local (server-side key; results shown live only, never stored — docs/03 §6)
    kakao_rest_api_key: SecretStr | None = None
    # Vercel Cron sends "Authorization: Bearer <CRON_SECRET>" to /api/v1/internal/cleanup; unset = endpoint closed
    cron_secret: SecretStr | None = None
    clinic_searches_per_minute: int = Field(default=30, ge=1)
    # public animal-hospital data (MOIS) for scripts/load_clinics.py only; the web app does not need it
    data_go_kr_service_key: SecretStr | None = None

    # MVP1 HITL (docs/04 §7). production = Vercel, or any non-local database (scripts run against Neon too):
    # operators cannot approve and mock knowledge cannot be activated there.
    app_env: str = Field(default_factory=lambda: "production" if os.environ.get("VERCEL") else "development",
                         pattern="^(production|development|test)$")
    # Review screens on their own host (e.g. hitl-review.pawcheck.ai, docs/05 S10; vet.pawcheck.ai is kept for the clinic screens of M03). Empty: /review on any host (local,
    # previews). Set: that host serves only the review screens + review API, other hosts do not serve the review API.
    review_host: str = ""
    # M03 guardian–vet link (docs/04 §7-1): one switch for screens and API; off until the real service opens.
    # VET_HOST (e.g. vet.pawcheck.ai) answers only the vet API, like REVIEW_HOST.
    m03_enabled: bool = False
    # 수의사 상담 스위치 시험 모드: only guardians whose profile opened /?pilot=<M03_PILOT_CODE> can use it, and every
    # question they send is test data (HITL copies stay test; knowledge from them cannot be published). Ignored when
    # M03_ENABLED is true (fully open).
    m03_pilot: bool = False
    m03_pilot_code: SecretStr | None = None
    vet_host: str = ""
    vet_session_hours: int = Field(default=12, ge=1, le=168)
    vet_logins_per_minute: int = Field(default=10, ge=1)
    reviewer_session_hours: int = Field(default=12, ge=1, le=168)
    review_logins_per_minute: int = Field(default=10, ge=1)
    hitl_max_spillover: float = Field(default=0.10, ge=0.0, le=1.0)  # search-impact check (not a quality verdict)
    hitl_min_eval_questions: int = Field(default=30, ge=1)  # fewer comparable questions -> check is invalid

    @property
    def hitl_production(self) -> bool:
        from sqlalchemy.engine import make_url

        host = make_url(self.database_url).host or ""
        return self.app_env == "production" or host not in {"localhost", "127.0.0.1", "::1"}


    @field_validator("llm_model", "llm_classifier_model", mode="before")
    @classmethod
    def _empty_means_default(cls, v, info):
        return cls.model_fields[info.field_name].default if v in (None, "") else v

    @property
    def origins(self) -> set[str]:
        out = {o.strip().rstrip("/") for o in self.allowed_origins.split(",") if o.strip()}
        # on Vercel, the deployment's own *.vercel.app hosts are always same-origin for this app
        for var in ("VERCEL_URL", "VERCEL_BRANCH_URL", "VERCEL_PROJECT_PRODUCTION_URL"):
            if os.environ.get(var):
                out.add("https://" + os.environ[var].strip().rstrip("/"))
        return out


@lru_cache
def get_settings() -> Settings:
    return Settings()
