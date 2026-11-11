"""Session, consultation, question and run handling (docs/04 §2-§3). Framework-free: the API layer
maps the ApiError codes raised here to HTTP responses."""
from __future__ import annotations

import hashlib
import secrets
import time
import uuid
from collections import defaultdict, deque
from datetime import date, datetime, timedelta, timezone
from threading import Lock

from sqlalchemy import delete, func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session as DB

from ..api.errors import ApiError
from ..config import Settings
from ..db.models import AnswerRun, Chunk, Citation, Consultation, Document, DocumentVersion, Feedback, Message
from ..db.models import Session as SessionRow
from ..domain.dog import DogProfileIn, derive_life_stage

KST = timezone(timedelta(hours=9))

RUN_ERRORS = {
    "model_error": "답변을 가져오지 못했어요. 잠시 후 다시 시도해 주세요.",
    "timeout": "답변 준비가 너무 오래 걸려 중단했어요. 다시 시도해 주세요.",
    "interrupted": "서버가 다시 시작되어 답변이 중단됐어요. 다시 시도해 주세요.",
    "internal_error": "답변을 가져오지 못했어요. 잠시 후 다시 시도해 주세요.",
}


def now() -> datetime:
    return datetime.now(timezone.utc)


def sha256(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


# ------------------------------------------------------------------ sessions
def create_session(db: DB, settings: Settings) -> tuple[str, SessionRow]:
    token = secrets.token_urlsafe(32)
    row = SessionRow(token_hash=sha256(token), expires_at=now() + timedelta(days=settings.session_ttl_days))
    db.add(row)
    db.commit()
    return token, row


def resolve_session(db: DB, token: str | None) -> SessionRow | None:
    if not token:
        return None
    row = db.execute(select(SessionRow).where(SessionRow.token_hash == sha256(token))).scalar_one_or_none()
    if row is None or row.expires_at <= now():
        return None
    return row


# ------------------------------------------------------------------ consultations
def today_kst() -> date:
    """The service runs for Korean guardians: ages and birthdays follow the Korean calendar day."""
    return datetime.now(KST).date()


def create_consultation(db: DB, settings: Settings, session: SessionRow, dog: DogProfileIn) -> Consultation:
    try:
        age = dog.age_on(today_kst())
    except ValueError:
        raise ApiError(422, "invalid_birth_date", extra={"fields": ["dog.birth_year"]})
    c = Consultation(
        session_id=session.id, dog_name=dog.name or None, dog_breed=dog.breed, dog_age_months=age, dog_birth_date=dog.birth_date(),
        dog_age_basis=dog.age_basis.value, dog_weight_kg=dog.weight_kg,
        dog_neutered=dog.neutered.value if dog.neutered else None, dog_health_notes=dog.health_notes or None,
        life_stage=derive_life_stage(age, settings.life_stage_puppy_max_months,
                                     settings.life_stage_senior_min_months).value,
    )
    db.add(c)
    db.commit()
    return c


def own_consultation(db: DB, session: SessionRow, consultation_id: uuid.UUID, lock: bool = False) -> Consultation:
    q = select(Consultation).where(Consultation.id == consultation_id, Consultation.session_id == session.id)
    if lock:
        q = q.with_for_update()
    c = db.execute(q).scalar_one_or_none()
    if c is None:  # missing and "someone else's" look the same (docs/04 §3)
        raise ApiError(404, "not_found")
    return c


def delete_consultation(db: DB, session: SessionRow, consultation_id: uuid.UUID) -> None:
    c = own_consultation(db, session, consultation_id, lock=True)
    db.execute(delete(Consultation).where(Consultation.id == c.id))  # cascades to messages, runs, citations, feedback
    db.commit()


# ------------------------------------------------------------------ questions
class RateLimiter:
    """Per-session sliding window, in process memory (single API process in the PoC)."""

    def __init__(self) -> None:
        self._hits: dict[uuid.UUID, deque] = defaultdict(deque)
        self._lock = Lock()

    def allow(self, key: uuid.UUID, limit: int, window: float = 60.0) -> bool:
        t = time.monotonic()
        with self._lock:
            q = self._hits[key]
            while q and t - q[0] > window:
                q.popleft()
            if len(q) >= limit:
                return False
            q.append(t)
            return True


def submit_question(db: DB, settings: Settings, limiter: RateLimiter, session: SessionRow,
                    consultation_id: uuid.UUID, question: str, client_request_id: str) -> AnswerRun:
    question = question.strip()
    if not question:
        raise ApiError(400, "empty_question")
    if len(question) > settings.question_max_chars:
        raise ApiError(400, "question_too_long", extra={"max_chars": settings.question_max_chars})
    c = own_consultation(db, session, consultation_id, lock=True)  # row lock serialises concurrent submits
    req_hash = sha256(question)

    # 1) re-send of the same request id comes first, so a retry never gets "busy" (docs/04 §3)
    existing = db.execute(select(AnswerRun).where(AnswerRun.consultation_id == c.id,
                                                  AnswerRun.client_request_id == client_request_id)).scalar_one_or_none()
    if existing is not None:
        if existing.request_hash != req_hash:
            raise ApiError(409, "request_conflict")
        if existing.status == "failed":  # explicit retry: same run back to the queue, question stored once
            _ensure_not_busy(db, c.id)
            existing.status, existing.error_code, existing.attempt = "queued", None, existing.attempt + 1
            existing.started_at = existing.finished_at = None
        db.commit()
        return existing

    # 2) one question at a time per consultation
    _ensure_not_busy(db, c.id)
    if not limiter.allow(session.id, settings.questions_per_minute):
        raise ApiError(429, "rate_limited")
    seq = (db.execute(select(func.max(Message.sequence)).where(Message.consultation_id == c.id)).scalar() or 0) + 1
    msg = Message(consultation_id=c.id, sequence=seq, role="user", content=question)
    db.add(msg)
    db.flush()
    run = AnswerRun(consultation_id=c.id, question_message_id=msg.id, client_request_id=client_request_id,
                    request_hash=req_hash, status="queued")
    db.add(run)
    db.commit()
    return run


def _ensure_not_busy(db: DB, consultation_id: uuid.UUID) -> None:
    busy = db.execute(select(AnswerRun.id).where(AnswerRun.consultation_id == consultation_id,
                                                 AnswerRun.status.in_(("queued", "running"))).limit(1)).first()
    if busy:
        raise ApiError(409, "busy")


# ------------------------------------------------------------------ reads
def citation_list(db: DB, run_ids: list[uuid.UUID]) -> dict[uuid.UUID, list[dict]]:
    if not run_ids:
        return {}
    rows = db.execute(
        select(Citation.id, Citation.answer_run_id, Citation.rank, Citation.title_snapshot,
               Document.document_type, Document.department)
        .outerjoin(Chunk, Chunk.id == Citation.chunk_id)
        .outerjoin(DocumentVersion, DocumentVersion.id == Chunk.document_version_id)
        .outerjoin(Document, Document.id == DocumentVersion.document_id)
        .where(Citation.answer_run_id.in_(run_ids)).order_by(Citation.answer_run_id, Citation.rank)
    ).all()
    out: dict[uuid.UUID, list[dict]] = defaultdict(list)
    for r in rows:
        out[r.answer_run_id].append({"id": str(r.id), "title": r.title_snapshot, "document_type": r.document_type,
                                     "department": r.department})
    return out


def run_view(db: DB, run: AnswerRun, citations: list[dict] | None = None) -> dict:
    v = {"id": str(run.id), "question_message_id": str(run.question_message_id),
         "client_request_id": run.client_request_id, "status": run.status, "response_type": run.response_type,
         "answer": None, "citations": [], "error": None}
    if run.status == "completed" and run.answer_message_id:
        msg = db.get(Message, run.answer_message_id)
        v["answer"] = {"message_id": str(run.answer_message_id), "text": msg.content if msg else ""}
        v["citations"] = citations if citations is not None else citation_list(db, [run.id]).get(run.id, [])
    if run.status == "failed":
        code = run.error_code or "internal_error"
        v["error"] = {"code": code, "message": RUN_ERRORS.get(code, RUN_ERRORS["internal_error"]), "retryable": True}
    return v


def get_run(db: DB, session: SessionRow, run_id: uuid.UUID) -> dict:
    run = db.execute(select(AnswerRun).join(Consultation, Consultation.id == AnswerRun.consultation_id)
                     .where(AnswerRun.id == run_id, Consultation.session_id == session.id)).scalar_one_or_none()
    if run is None:
        raise ApiError(404, "not_found")
    return run_view(db, run)


def dog_view(c: Consultation) -> dict:
    """Birth parts are returned as far as they were entered (the form is prefilled with them and the day is
    kept for birthdays); screens show only the age."""
    b, basis = c.dog_birth_date, c.dog_age_basis
    birth = {"birth_year": b.year if b else None,
             "birth_month": b.month if b and basis in ("day", "month") else None,
             "birth_day": b.day if b and basis == "day" else None}
    return {"name": c.dog_name, "breed": c.dog_breed, "age_months": c.dog_age_months, "age_basis": basis, **birth,
            "weight_kg": float(c.dog_weight_kg) if c.dog_weight_kg is not None else None,
            "neutered": c.dog_neutered, "health_notes": c.dog_health_notes}


def consultation_messages(db: DB, session: SessionRow, consultation_id: uuid.UUID) -> dict:
    c = own_consultation(db, session, consultation_id)
    msgs = db.execute(select(Message).where(Message.consultation_id == c.id).order_by(Message.sequence)).scalars().all()
    runs = db.execute(select(AnswerRun).where(AnswerRun.consultation_id == c.id).order_by(AnswerRun.created_at)).scalars().all()
    cites = citation_list(db, [r.id for r in runs if r.status == "completed"])
    by_answer = {r.answer_message_id: r for r in runs if r.answer_message_id}
    fb = {f.answer_message_id: f for f in db.execute(select(Feedback).where(
        Feedback.session_id == session.id, Feedback.answer_message_id.in_([m.id for m in msgs]))).scalars()}
    out = []
    for m in msgs:
        item = {"id": str(m.id), "role": m.role, "content": m.content, "created_at": m.created_at.isoformat()}
        if m.role == "assistant":
            r = by_answer.get(m.id)
            item["run_id"] = str(r.id) if r else None
            item["response_type"] = r.response_type if r else None
            item["citations"] = cites.get(r.id, []) if r else []
            f = fb.get(m.id)
            item["feedback"] = {"helpful": f.helpful, "comment": f.comment} if f else None
        out.append(item)
    return {"consultation_id": str(c.id), "dog": dog_view(c), "life_stage": c.life_stage,
            "created_at": c.created_at.isoformat(), "messages": out,
            "runs": [run_view(db, r, cites.get(r.id, [])) for r in runs]}


def citation_detail(db: DB, settings: Settings, session: SessionRow, citation_id: uuid.UUID) -> dict:
    row = db.execute(
        select(Citation, Document, Chunk)
        .join(AnswerRun, AnswerRun.id == Citation.answer_run_id)
        .join(Consultation, Consultation.id == AnswerRun.consultation_id)
        .outerjoin(Chunk, Chunk.id == Citation.chunk_id)
        .outerjoin(DocumentVersion, DocumentVersion.id == Chunk.document_version_id)
        .outerjoin(Document, Document.id == DocumentVersion.document_id)
        .where(Citation.id == citation_id, Consultation.session_id == session.id)
    ).first()
    if row is None:
        raise ApiError(404, "not_found")
    cit, doc, chunk = row
    available = chunk is not None and doc is not None and doc.status == "active"
    if not available:
        return {"id": str(cit.id), "title": cit.title_snapshot, "availability": "unavailable", "document_type": None,
                "department": None, "source_name": None, "source_uri": None, "excerpt": None, "excerpt_allowed": False}
    # an excerpt leaves the server only when the feature is on AND this document's terms allow it (docs/05 §4)
    excerpt_allowed = bool(settings.enable_source_excerpts and doc.excerpt_allowed)
    uri = doc.source_uri if doc.source_uri and doc.source_uri.startswith(("https://", "http://")) else None
    return {"id": str(cit.id), "title": cit.title_snapshot, "availability": "available",
            "document_type": doc.document_type, "department": doc.department, "source_name": doc.source_name,
            "source_uri": uri, "excerpt": chunk.content[:500] if excerpt_allowed else None,
            "excerpt_allowed": excerpt_allowed}


# ------------------------------------------------------------------ feedback
def put_feedback(db: DB, session: SessionRow, message_id: uuid.UUID, helpful: bool, comment: str | None) -> uuid.UUID:
    ok = db.execute(select(Message.id).join(Consultation, Consultation.id == Message.consultation_id)
                    .where(Message.id == message_id, Message.role == "assistant",
                           Consultation.session_id == session.id)).first()
    if not ok:
        raise ApiError(404, "not_found")
    stmt = pg_insert(Feedback).values(id=uuid.uuid4(), answer_message_id=message_id, session_id=session.id,
                                      helpful=helpful, comment=comment or None)
    stmt = stmt.on_conflict_do_update(constraint="uq_feedback_once",
                                      set_={"helpful": helpful, "comment": comment or None, "updated_at": func.now()})
    fid = db.execute(stmt.returning(Feedback.id)).scalar_one()
    db.commit()
    return fid


# ------------------------------------------------------------------ retention (docs/03 §3)
def purge_expired_sessions(db: DB, grace_hours: int = 24) -> int:
    """Delete sessions expired more than `grace_hours` ago, with all their consultations (cascade)."""
    n = db.execute(text("DELETE FROM session WHERE expires_at < now() - make_interval(hours => :h)"),
                   {"h": grace_hours}).rowcount
    db.commit()
    return n
