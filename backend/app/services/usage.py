"""서비스 이용통계 (operator, hitl-review): Flow per KST day and Stock levels, counts only.

- Flow of a day is computed from the raw records while they are still kept (session_ttl_days + 1 day margin) and
  stored in usage_daily; after that the stored numbers are frozen (raw consultations get deleted by retention).
- Stock is measured now; today's row keeps the latest stock, so past days show the last level written that day.
- fill() is idempotent: run it from the page (lazy) and from the daily cleanup job BEFORE purging.
No question text, ids, dog names or other personal data are read into the result.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session as DB

from ..config import Settings
from ..db.models_stats import UsageDaily
from .consult import KST, now, today_kst

DAY = "(({col}) AT TIME ZONE 'Asia/Seoul')::date"

# counts per KST day: name -> (table, timestamp column, extra condition)
FLOW_COUNTS = {
    "sessions": ("session", "created_at", ""),
    "consultations": ("consultation", "created_at", ""),
    "questions": ("message", "created_at", "role = 'user'"),
    "answers": ("answer_run", "created_at", "status = 'completed'"),
    "failed": ("answer_run", "created_at", "status = 'failed'"),
    "grounded": ("answer_run", "created_at", "status = 'completed' AND response_type = 'grounded'"),
    "clarification": ("answer_run", "created_at", "status = 'completed' AND response_type = 'clarification'"),
    "insufficient_evidence": ("answer_run", "created_at", "status = 'completed' AND response_type = 'insufficient_evidence'"),
    "out_of_scope": ("answer_run", "created_at", "status = 'completed' AND response_type = 'out_of_scope'"),
    "feedback_helpful": ("feedback", "created_at", "helpful"),
    "feedback_unhelpful": ("feedback", "created_at", "NOT helpful"),
    "profiles_created": ("dog_profile", "created_at", ""),
    "vet_links_requested": ("vet_link", "requested_at", ""),
    "vet_questions_sent": ("vet_question", "created_at", ""),
    "vet_replies": ("vet_reply_revision", "created_at", "kind = 'original'"),
}

STOCK_SQL = {
    "sessions_live": "SELECT count(*) FROM session WHERE expires_at > now()",
    "consultations_kept": "SELECT count(*) FROM consultation",
    "profiles_kept": "SELECT count(*) FROM dog_profile WHERE expires_at IS NULL OR expires_at > now()",
    "profiles_with_clinic": "SELECT count(*) FROM dog_profile WHERE (expires_at IS NULL OR expires_at > now()) AND clinic_id IS NOT NULL",
    "vets_active": "SELECT count(*) FROM vet_account WHERE active AND NOT is_test",
    "vet_links_accepted": "SELECT count(*) FROM vet_link WHERE status = 'accepted'",
    "vet_questions_waiting": "SELECT count(*) FROM vet_question WHERE status = 'sent'",
    "review_items": "SELECT count(*) FROM review_item WHERE NOT is_mock",
    "knowledge_active": "SELECT count(*) FROM knowledge_candidate WHERE publish_status = 'active' AND NOT is_mock",
    "documents_active": "SELECT count(*) FROM document WHERE status = 'active' AND active_version_id IS NOT NULL",
}


def _kst_bounds(day: date) -> tuple[datetime, datetime]:
    start = datetime(day.year, day.month, day.day, tzinfo=KST)
    return start, start + timedelta(days=1)


def flow_for(db: DB, day: date) -> dict:
    start, end = _kst_bounds(day)
    out: dict = {}
    for name, (table, col, cond) in FLOW_COUNTS.items():
        where = f"{col} >= :s AND {col} < :e" + (f" AND {cond}" if cond else "")
        out[name] = db.execute(text(f"SELECT count(*) FROM {table} WHERE {where}"), {"s": start, "e": end}).scalar()
    out["answer_ms_avg"] = db.execute(text(
        "SELECT round(avg(latency_ms)) FROM answer_run WHERE status = 'completed' AND created_at >= :s AND created_at < :e"),
        {"s": start, "e": end}).scalar()
    out["answer_ms_avg"] = int(out["answer_ms_avg"]) if out["answer_ms_avg"] is not None else None
    topics = db.execute(text("SELECT topic, count(*) FROM consultation WHERE topic IS NOT NULL AND created_at >= :s "
                             "AND created_at < :e GROUP BY topic"), {"s": start, "e": end}).all()
    out["topics"] = {t: n for t, n in topics}
    return out


def stock_now(db: DB) -> dict:
    return {k: db.execute(text(q)).scalar() for k, q in STOCK_SQL.items()}


def _earliest_raw_day(db: DB) -> date | None:
    first = db.execute(text("SELECT min(t) FROM (SELECT min(created_at) t FROM session UNION ALL "
                            "SELECT min(created_at) FROM consultation UNION ALL SELECT min(created_at) FROM dog_profile) x")).scalar()
    return first.astimezone(KST).date() if first else None


def fill(db: DB, settings: Settings) -> dict:
    """Store flow for every day whose raw records are still complete (and any missing earlier day once), and today's
    stock. Days older than the retention window keep their stored flow unchanged."""
    today = today_kst()
    keep_from = today - timedelta(days=settings.session_ttl_days + 1)
    stored = {r.day: r for r in db.query(UsageDaily).all()}
    first = _earliest_raw_day(db) or today
    written = 0
    d = min(first, today)
    while d <= today:
        if d >= keep_from or d not in stored:  # recompute while complete; older days only if never stored
            row = {"day": d, "flow": flow_for(db, d), "flow_at": now()}
            stmt = insert(UsageDaily).values(**row).on_conflict_do_update(index_elements=["day"],
                                                                          set_={"flow": row["flow"], "flow_at": row["flow_at"]})
            db.execute(stmt)
            written += 1
        d += timedelta(days=1)
    s = stock_now(db)
    db.execute(insert(UsageDaily).values(day=today, flow={}, stock=s, stock_at=now())
               .on_conflict_do_update(index_elements=["day"], set_={"stock": s, "stock_at": now()}))
    db.commit()
    return {"days_written": written, "stock_day": today.isoformat()}


def report(db: DB, settings: Settings, days: int | None) -> dict:
    fill(db, settings)
    today = today_kst()
    q = db.query(UsageDaily).order_by(UsageDaily.day)
    if days:
        q = q.filter(UsageDaily.day > today - timedelta(days=days))
    rows = q.all()
    keys = [k for k in FLOW_COUNTS]
    totals = {k: sum((r.flow or {}).get(k, 0) or 0 for r in rows) for k in keys}
    ms = [((r.flow or {}).get("answer_ms_avg"), (r.flow or {}).get("answers", 0)) for r in rows]
    weighted = [(m, n) for m, n in ms if m is not None and n]
    totals["answer_ms_avg"] = round(sum(m * n for m, n in weighted) / sum(n for _, n in weighted)) if weighted else None
    topics: dict = {}
    for r in rows:
        for t, n in ((r.flow or {}).get("topics") or {}).items():
            topics[t] = topics.get(t, 0) + n
    totals["topics"] = topics
    return {
        "days": days, "today": today.isoformat(), "retention_days": settings.session_ttl_days,
        "flow": {"totals": totals, "by_day": [{"day": r.day.isoformat(), **(r.flow or {})} for r in rows]},
        "stock": {"now": stock_now(db),
                  "by_day": [{"day": r.day.isoformat(), **r.stock} for r in rows if r.stock]},
    }
