"""Daily retention job (docs/03 §3): store the day's usage counts first, then delete what has expired.

Used by scripts/cleanup_sessions.py and by the Vercel Cron endpoint GET /api/v1/internal/cleanup. Returns counts only.
"""
from __future__ import annotations

from sqlalchemy.orm import Session as DB

from ..config import Settings
from .consult import purge_expired_sessions
from .profile import purge_expired_profiles
from .usage import fill as fill_usage


def run_cleanup(db: DB, settings: Settings, grace_hours: int = 24) -> dict:
    u = fill_usage(db, settings)  # 서비스 이용통계: counts are stored BEFORE raw records are purged
    sessions = purge_expired_sessions(db, grace_hours)
    profiles = purge_expired_profiles(db)  # also removes M03 questions and their HITL copies (rules in m03_hitl)
    return {"usage_days_stored": u["days_written"], "sessions_deleted": sessions, "profiles_deleted": profiles}
