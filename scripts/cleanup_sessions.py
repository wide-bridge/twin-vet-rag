"""Retention job (docs/03 §3): permanently delete sessions that expired more than 24 hours ago,
together with their consultations, messages, runs, citations and feedback (FK cascade).
Also deletes dog profiles (settings) past their expiry (last use + PROFILE_TTL_DAYS).
Run once a day (cron / scheduled task). Logs only the count — never ids or content.

    python -m scripts.cleanup_sessions [--grace-hours 24]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app.db.engine import session_factory  # noqa: E402
from backend.app.services.consult import purge_expired_sessions  # noqa: E402
from backend.app.services.profile import purge_expired_profiles  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--grace-hours", type=int, default=24)
    args = ap.parse_args()
    with session_factory()() as db:
        n = purge_expired_sessions(db, args.grace_hours)
        p = purge_expired_profiles(db)
    print(f"deleted {n} expired session(s), {p} expired profile(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
