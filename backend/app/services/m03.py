"""M03 guardian–vet question/answer link (docs/01 §4-1, docs/02 §5, docs/03 §4-1, docs/04 §7-1).

Access rules (all enforced here, never in the screens):
- a guardian is a dog profile (anonymous cookie); they see only their own links and questions;
- a vet sees only links and questions addressed to them; content of a question is never shown before the link is
  accepted, and a cancelled question shows no content;
- missing and "someone else's" look the same (404).
Everything is deleted with the dog profile (FK cascade). Guardian actions extend the profile's retention; vet
actions never do (docs/03 §4-1).
"""
from __future__ import annotations

import secrets
import uuid
from datetime import timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as DB

from ..api.errors import ApiError
from ..config import Settings
from ..db.models import AnswerRun, Consultation, DogProfile, Message, Session as SessionRow
from ..db.models_m03 import VetAccount, VetLink, VetQuestion, VetReplyRevision, VetSession
from . import profile as profile_svc
from .consult import citation_list, now, sha256

SHAREABLE_FIELDS = ("name", "breed", "age", "weight", "neutered", "health_notes")
DEFAULT_FIELDS = ("breed", "age", "weight", "neutered")
NOTE_MAX = 500
REPLY_MAX = 4000


def require_enabled(settings: Settings) -> None:
    if not settings.m03_enabled:
        raise ApiError(404, "not_found")


# ---------------------------------------------------------------- vet accounts and login
def create_vet(db: DB, display_name: str, clinic_name: str | None = None, intro: str | None = None,
               is_test: bool = False) -> tuple[VetAccount, str]:
    """Returns the account and the raw access key (shown once; only its hash is stored)."""
    raw = "tvv_" + secrets.token_urlsafe(24)
    v = VetAccount(display_name=display_name.strip()[:40], clinic_name=(clinic_name or "").strip()[:100] or None,
                   intro=(intro or "").strip()[:300] or None, is_test=is_test, access_key_hash=sha256(raw))
    db.add(v)
    db.commit()
    return v, raw


def vet_login(db: DB, raw_key: str, hours: int) -> tuple[VetAccount, str] | None:
    v = db.execute(select(VetAccount).where(VetAccount.access_key_hash == sha256(raw_key.strip()))).scalar_one_or_none()
    if v is None or not v.active:
        return None
    token = secrets.token_urlsafe(32)
    db.add(VetSession(vet_id=v.id, token_hash=sha256(token), expires_at=now() + timedelta(hours=hours)))
    v.last_login_at = now()
    db.commit()
    return v, token


def vet_for_token(db: DB, token: str | None) -> VetAccount | None:
    if not token:
        return None
    return db.execute(
        select(VetAccount).join(VetSession, VetSession.vet_id == VetAccount.id)
        .where(VetSession.token_hash == sha256(token), VetSession.expires_at > now(), VetAccount.active.is_(True))
    ).scalar_one_or_none()


def vet_logout(db: DB, token: str | None) -> None:
    if token:
        db.execute(delete(VetSession).where(VetSession.token_hash == sha256(token)))
        db.commit()


def vet_view(v: VetAccount) -> dict:
    return {"id": str(v.id), "display_name": v.display_name, "clinic_name": v.clinic_name, "intro": v.intro,
            "is_test": v.is_test}


def _visible(settings: Settings, v: VetAccount | None) -> bool:
    return v is not None and v.active and (not v.is_test or settings.app_env != "production")


# ---------------------------------------------------------------- guardian: vets and links
def list_vets(db: DB, settings: Settings) -> list[dict]:
    rows = db.execute(select(VetAccount).where(VetAccount.active.is_(True)).order_by(VetAccount.display_name)).scalars()
    return [vet_view(v) for v in rows if _visible(settings, v)]


def link_view(db: DB, link: VetLink) -> dict:
    v = db.get(VetAccount, link.vet_id)
    return {"id": str(link.id), "status": link.status, "vet": vet_view(v) if v else None,
            "requested_at": link.requested_at.isoformat(), "decided_at": link.decided_at.isoformat() if link.decided_at else None,
            "ended_at": link.ended_at.isoformat() if link.ended_at else None, "ended_by": link.ended_by}


def request_link(db: DB, settings: Settings, prof: DogProfile, vet_id: uuid.UUID) -> dict:
    v = db.get(VetAccount, vet_id)
    if not _visible(settings, v):
        raise ApiError(404, "not_found")
    link = VetLink(profile_id=prof.id, vet_id=v.id, status="requested")
    db.add(link)
    profile_svc.touch(db, settings, prof)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise ApiError(409, "link_exists")
    return link_view(db, link)


def my_links(db: DB, settings: Settings, prof: DogProfile) -> list[dict]:
    profile_svc.touch(db, settings, prof)
    db.commit()
    rows = db.execute(select(VetLink).where(VetLink.profile_id == prof.id).order_by(VetLink.requested_at.desc())).scalars()
    return [link_view(db, x) for x in rows]


def _own_link(db: DB, prof: DogProfile, link_id: uuid.UUID, lock: bool = False) -> VetLink:
    q = select(VetLink).where(VetLink.id == link_id, VetLink.profile_id == prof.id)
    link = db.execute(q.with_for_update() if lock else q).scalar_one_or_none()
    if link is None:
        raise ApiError(404, "not_found")
    return link


def _end_link(db: DB, link: VetLink, by: str) -> None:
    """requested -> cancelled (guardian) / declined is separate; accepted -> ended, pending questions cancelled."""
    t = now()
    if link.status == "requested" and by == "guardian":
        link.status, link.ended_at, link.ended_by = "cancelled", t, by
    elif link.status == "accepted":
        link.status, link.ended_at, link.ended_by = "ended", t, by
        for q in db.execute(select(VetQuestion).where(VetQuestion.link_id == link.id, VetQuestion.status == "sent")
                            .with_for_update()).scalars():
            q.status, q.cancelled_reason, q.access_token_hash = "cancelled", "link_ended", None
    else:
        raise ApiError(409, "link_closed")


def close_link(db: DB, settings: Settings, prof: DogProfile, link_id: uuid.UUID) -> dict:
    link = _own_link(db, prof, link_id, lock=True)
    _end_link(db, link, "guardian")
    profile_svc.touch(db, settings, prof)
    db.commit()
    return link_view(db, link)


# ---------------------------------------------------------------- guardian: questions
def _dog_fields(c: Consultation, fields: list[str]) -> dict:
    full = {"name": c.dog_name, "breed": c.dog_breed, "age": c.dog_age_months,
            "weight": float(c.dog_weight_kg) if c.dog_weight_kg is not None else None,
            "neutered": c.dog_neutered, "health_notes": c.dog_health_notes}
    return {k: full[k] for k in SHAREABLE_FIELDS if k in fields and full.get(k) is not None}  # fixed order


def send_question(db: DB, settings: Settings, prof: DogProfile, session: SessionRow, *, link_id: uuid.UUID,
                  answer_message_id: uuid.UUID, note: str | None, shared_fields: list[str],
                  quality_use_consent: bool) -> tuple[dict, str]:
    """Freeze the chosen question (+ AI answer, note, chosen dog fields) for the linked vet. Returns (view, raw link token)."""
    link = _own_link(db, prof, link_id, lock=True)
    if link.status != "accepted":
        raise ApiError(409, "link_not_accepted")
    if not _visible(settings, db.get(VetAccount, link.vet_id)):
        raise ApiError(409, "link_not_accepted")
    bad = [f for f in shared_fields if f not in SHAREABLE_FIELDS]
    if bad:
        raise ApiError(422, "invalid_input", extra={"fields": ["shared_fields"]})
    note = (note or "").strip() or None
    if note and len(note) > NOTE_MAX:
        raise ApiError(422, "invalid_input", extra={"fields": ["note_to_vet"]})
    # the answer must be in this browser's session AND belong to this dog profile
    row = db.execute(
        select(Message, Consultation, AnswerRun)
        .join(Consultation, Consultation.id == Message.consultation_id)
        .join(AnswerRun, AnswerRun.answer_message_id == Message.id)
        .where(Message.id == answer_message_id, Message.role == "assistant", Consultation.session_id == session.id,
               Consultation.profile_id == prof.id, AnswerRun.status == "completed")
    ).first()
    if row is None:
        raise ApiError(404, "not_found")
    msg, cons, run = row
    question = db.get(Message, run.question_message_id)
    cites = citation_list(db, [run.id]).get(run.id, [])
    snapshot = {"question": question.content if question else "", "ai_answer": msg.content,
                "ai_response_type": run.response_type, "ai_answered_at": msg.created_at.isoformat(),
                "sources": [c["title"] for c in cites], "note_to_vet": note,
                "dog": _dog_fields(cons, list(dict.fromkeys(shared_fields)))}
    token = secrets.token_urlsafe(32)
    t = now()
    q = VetQuestion(link_id=link.id, profile_id=prof.id, vet_id=link.vet_id, source_consultation_id=cons.id,
                    source_answer_message_id=msg.id, snapshot=snapshot | {"shared_at": t.isoformat()},
                    shared_fields=list(dict.fromkeys(shared_fields)), quality_use_consent=bool(quality_use_consent),
                    consent_changed_at=t, status="sent", access_token_hash=sha256(token))
    db.add(q)
    profile_svc.touch(db, settings, prof)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise ApiError(409, "question_exists")
    return guardian_question_view(db, q), token


def _revisions(db: DB, q: VetQuestion) -> list[VetReplyRevision]:
    return list(db.execute(select(VetReplyRevision).where(VetReplyRevision.question_id == q.id)
                           .order_by(VetReplyRevision.created_at, VetReplyRevision.id)).scalars())


def _reply_view(revs: list[VetReplyRevision]) -> dict | None:
    if not revs:
        return None
    last = revs[-1]
    return {"body": last.body, "sent_at": revs[0].created_at.isoformat(), "corrected": len(revs) > 1,
            "corrected_at": last.created_at.isoformat() if len(revs) > 1 else None,
            "history": [{"kind": r.kind, "body": r.body, "created_at": r.created_at.isoformat()} for r in revs]}


def guardian_question_view(db: DB, q: VetQuestion) -> dict:
    v = db.get(VetAccount, q.vet_id)
    return {"id": str(q.id), "link_id": str(q.link_id), "status": q.status, "cancelled_reason": q.cancelled_reason,
            "vet": vet_view(v) if v else None, "answer_message_id": str(q.source_answer_message_id) if q.source_answer_message_id else None,
            "snapshot": q.snapshot, "shared_fields": q.shared_fields, "quality_use_consent": q.quality_use_consent,
            "created_at": q.created_at.isoformat(), "reply": _reply_view(_revisions(db, q))}


def my_questions(db: DB, settings: Settings, prof: DogProfile) -> list[dict]:
    profile_svc.touch(db, settings, prof)
    db.commit()
    rows = db.execute(select(VetQuestion).where(VetQuestion.profile_id == prof.id).order_by(VetQuestion.created_at.desc())).scalars()
    return [guardian_question_view(db, q) for q in rows]


def _own_question(db: DB, prof: DogProfile, qid: uuid.UUID, lock: bool = False) -> VetQuestion:
    s = select(VetQuestion).where(VetQuestion.id == qid, VetQuestion.profile_id == prof.id)
    q = db.execute(s.with_for_update() if lock else s).scalar_one_or_none()
    if q is None:
        raise ApiError(404, "not_found")
    return q


def question_detail(db: DB, settings: Settings, prof: DogProfile, qid: uuid.UUID) -> dict:
    q = _own_question(db, prof, qid)
    if q.status == "answered" and q.guardian_seen_at is None:
        q.guardian_seen_at = now()
    profile_svc.touch(db, settings, prof)
    db.commit()
    return guardian_question_view(db, q)


def cancel_question(db: DB, settings: Settings, prof: DogProfile, qid: uuid.UUID) -> dict:
    q = _own_question(db, prof, qid, lock=True)
    if q.status != "sent":
        raise ApiError(409, "question_closed")
    q.status, q.cancelled_reason, q.access_token_hash = "cancelled", "guardian", None  # the link stops working
    profile_svc.touch(db, settings, prof)
    db.commit()
    return guardian_question_view(db, q)


def delete_question(db: DB, settings: Settings, prof: DogProfile, qid: uuid.UUID) -> None:
    """Permanent: the question, its reply history and the access link disappear for both sides."""
    q = _own_question(db, prof, qid, lock=True)
    db.delete(q)
    profile_svc.touch(db, settings, prof)
    db.commit()


def set_consent(db: DB, settings: Settings, prof: DogProfile, qid: uuid.UUID, consent: bool) -> dict:
    # row lock: the HITL import (later) takes the same lock and re-checks consent before saving (docs/01 §4-1)
    q = _own_question(db, prof, qid, lock=True)
    if q.quality_use_consent != consent:
        q.quality_use_consent, q.consent_changed_at = consent, now()
    profile_svc.touch(db, settings, prof)
    db.commit()
    return guardian_question_view(db, q)


def reissue_token(db: DB, settings: Settings, prof: DogProfile, qid: uuid.UUID) -> str:
    q = _own_question(db, prof, qid, lock=True)
    if q.status == "cancelled":
        raise ApiError(409, "question_closed")
    token = secrets.token_urlsafe(32)
    q.access_token_hash = sha256(token)  # the previous link stops working
    profile_svc.touch(db, settings, prof)
    db.commit()
    return token


def question_by_token(db: DB, settings: Settings, token: str) -> dict:
    """The confirmation link opens that one question only, while its dog profile is kept."""
    if not token or len(token) > 200:
        raise ApiError(404, "not_found")
    q = db.execute(select(VetQuestion).where(VetQuestion.access_token_hash == sha256(token))).scalar_one_or_none()
    prof = db.get(DogProfile, q.profile_id) if q else None
    if q is None or q.status == "cancelled" or prof is None or not _profile_alive(prof):
        raise ApiError(404, "not_found")
    if q.status == "answered" and q.guardian_seen_at is None:
        q.guardian_seen_at = now()
    profile_svc.touch(db, settings, prof)
    db.commit()
    return guardian_question_view(db, q)


# ---------------------------------------------------------------- vet side
def _profile_alive(prof: DogProfile) -> bool:
    return prof.expires_at is None or prof.expires_at > now()


def _alive():
    """SQL condition: the guardian's dog profile has not expired (cleanup may not have run yet)."""
    return select(DogProfile.id).where(DogProfile.id == VetQuestion.profile_id).where(
        (DogProfile.expires_at.is_(None)) | (DogProfile.expires_at > now())).exists()


def _alive_link():
    return select(DogProfile.id).where(DogProfile.id == VetLink.profile_id).where(
        (DogProfile.expires_at.is_(None)) | (DogProfile.expires_at > now())).exists()


def _vet_link(db: DB, vet: VetAccount, link_id: uuid.UUID, lock: bool = False) -> VetLink:
    s = select(VetLink).where(VetLink.id == link_id, VetLink.vet_id == vet.id, _alive_link())
    link = db.execute(s.with_for_update() if lock else s).scalar_one_or_none()
    if link is None:
        raise ApiError(404, "not_found")
    return link


def vet_link_view(link: VetLink, open_questions: int = 0) -> dict:
    """Before acceptance the vet sees only that someone asked and when (no consultation or dog details)."""
    return {"id": str(link.id), "status": link.status, "guardian_ref": str(link.profile_id)[:8],
            "requested_at": link.requested_at.isoformat(), "decided_at": link.decided_at.isoformat() if link.decided_at else None,
            "ended_at": link.ended_at.isoformat() if link.ended_at else None, "ended_by": link.ended_by,
            "open_questions": open_questions}


def vet_links(db: DB, vet: VetAccount) -> list[dict]:
    rows = db.execute(select(VetLink).where(VetLink.vet_id == vet.id, VetLink.status.in_(("requested", "accepted")), _alive_link())
                      .order_by(VetLink.requested_at.desc())).scalars().all()
    counts = dict(db.execute(select(VetQuestion.link_id, func.count()).where(
        VetQuestion.vet_id == vet.id, VetQuestion.status == "sent", _alive()).group_by(VetQuestion.link_id)).all())
    return [vet_link_view(x, counts.get(x.id, 0)) for x in rows]


def decide_link(db: DB, vet: VetAccount, link_id: uuid.UUID, accept: bool) -> dict:
    link = _vet_link(db, vet, link_id, lock=True)
    if link.status != "requested":
        raise ApiError(409, "link_closed")
    link.status, link.decided_at = ("accepted" if accept else "declined"), now()
    db.commit()
    return vet_link_view(link)


def vet_end_link(db: DB, vet: VetAccount, link_id: uuid.UUID) -> dict:
    link = _vet_link(db, vet, link_id, lock=True)
    if link.status != "accepted":
        raise ApiError(409, "link_closed")
    _end_link(db, link, "vet")
    db.commit()
    return vet_link_view(link)


def vet_question_view(db: DB, q: VetQuestion, full: bool) -> dict:
    v = {"id": str(q.id), "link_id": str(q.link_id), "status": q.status, "cancelled_reason": q.cancelled_reason,
         "created_at": q.created_at.isoformat(), "replied_at": q.replied_at.isoformat() if q.replied_at else None,
         "guardian_ref": str(q.profile_id)[:8]}
    if q.status == "cancelled":
        return v  # a cancelled question shows no content
    v["question_preview"] = q.snapshot.get("question", "")[:80]
    if full:
        v["snapshot"] = q.snapshot
        v["reply_draft"] = q.reply_draft
        v["reply"] = _reply_view(_revisions(db, q))
    return v


def vet_questions(db: DB, vet: VetAccount) -> list[dict]:
    rows = db.execute(select(VetQuestion).where(VetQuestion.vet_id == vet.id, _alive()).order_by(VetQuestion.created_at.desc())).scalars()
    return [vet_question_view(db, q, full=False) for q in rows]


def _vet_question(db: DB, vet: VetAccount, qid: uuid.UUID, lock: bool = False) -> VetQuestion:
    s = select(VetQuestion).where(VetQuestion.id == qid, VetQuestion.vet_id == vet.id, _alive())
    q = db.execute(s.with_for_update() if lock else s).scalar_one_or_none()
    if q is None:
        raise ApiError(404, "not_found")
    return q


def vet_question_detail(db: DB, vet: VetAccount, qid: uuid.UUID) -> dict:
    return vet_question_view(db, _vet_question(db, vet, qid), full=True)


def _body(text: str | None) -> str:
    body = (text or "").strip()
    if not body:
        raise ApiError(422, "empty_reply")
    if len(body) > REPLY_MAX:
        raise ApiError(422, "reply_too_long")
    return body


def save_draft(db: DB, vet: VetAccount, qid: uuid.UUID, text: str | None) -> dict:
    q = _vet_question(db, vet, qid, lock=True)
    if q.status != "sent":
        raise ApiError(409, "question_closed")
    q.reply_draft = (text or "")[:REPLY_MAX] or None
    db.commit()
    return vet_question_view(db, q, full=True)


def send_reply(db: DB, vet: VetAccount, qid: uuid.UUID, text: str | None) -> dict:
    q = _vet_question(db, vet, qid, lock=True)
    if q.status != "sent":
        raise ApiError(409, "question_closed")
    body = _body(text if text is not None else q.reply_draft)
    db.add(VetReplyRevision(question_id=q.id, kind="original", body=body, created_at=now()))
    q.status, q.replied_at, q.reply_draft = "answered", now(), None
    db.commit()
    return vet_question_view(db, q, full=True)


def correct_reply(db: DB, vet: VetAccount, qid: uuid.UUID, text: str | None) -> dict:
    """A correction is a new row; the original and earlier corrections are kept unchanged."""
    q = _vet_question(db, vet, qid, lock=True)
    if q.status != "answered":
        raise ApiError(409, "question_not_answered")
    db.add(VetReplyRevision(question_id=q.id, kind="correction", body=_body(text), created_at=now()))
    q.guardian_seen_at = None  # the guardian has not seen the correction yet
    db.commit()
    return vet_question_view(db, q, full=True)
