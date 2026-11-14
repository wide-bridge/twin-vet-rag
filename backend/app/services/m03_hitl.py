"""M03 → HITL (docs/01 §4-1 '②→③ 연결 규칙', 'HITL 이관 조건'): operator stats, the import queue, and the rules that
keep withdrawal / deletion / correction consistent all the way into HITL.

- Only questions with quality-use consent, a sent reply and a live dog profile can be imported, by an operator, from
  text the operator has read and cleaned. The copy stays linked to the shared question (review_item.vet_question_id).
- Import and withdrawal/deletion lock the same vet_question row, and import re-checks consent, existence, reply and
  expiry under that lock: whichever order they run in, no copy survives a withdrawal.
- A correction after import flags the copy 'needs_recheck'; labels and the recorded reply are never overwritten.
- When a copy is removed, knowledge candidates made from it lose their approval and are blocked; published knowledge
  from it is taken out of search (document inactive) before the copy is deleted.
Stats are numbers only; question bodies are visible to operators only for importable (consented + answered) questions.
"""
from __future__ import annotations

import statistics
import uuid
from datetime import timedelta

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session as DB

from ..api.errors import ApiError
from ..db.models import DogProfile, Document
from ..db.models_m03 import VetLink, VetQuestion, VetReplyRevision
from ..db.models_review import KnowledgeCandidate, Reviewer, ReviewEvent, ReviewItem
from ..ingest import load
from .consult import now

VET_DATASET_CODE = "vetreview"  # same as services.review (kept literal to avoid an import cycle)
TEXT_MAX = 4000


def _alive_q():
    return select(DogProfile.id).where(DogProfile.id == VetQuestion.profile_id).where(
        (DogProfile.expires_at.is_(None)) | (DogProfile.expires_at > now())).exists()


def _latest_revision(db: DB, qid: uuid.UUID) -> VetReplyRevision | None:
    return db.execute(select(VetReplyRevision).where(VetReplyRevision.question_id == qid)
                      .order_by(VetReplyRevision.created_at.desc(), VetReplyRevision.id.desc()).limit(1)).scalar_one_or_none()


# ---------------------------------------------------------------- stats (numbers only; screen and CLI share this)
def stats(db: DB, days: int | None = None) -> dict:
    since = now() - timedelta(days=days) if days else None
    lq = select(VetLink.status, func.count()).group_by(VetLink.status)
    qq = select(VetQuestion.status, func.count()).group_by(VetQuestion.status)
    if since:
        lq, qq = lq.where(VetLink.requested_at >= since), qq.where(VetQuestion.created_at >= since)
    links = dict(db.execute(lq).all())
    questions = dict(db.execute(qq).all())
    rq = select(VetQuestion.created_at, VetQuestion.replied_at).where(VetQuestion.replied_at.is_not(None))
    cq = select(VetQuestion.quality_use_consent, func.count()).group_by(VetQuestion.quality_use_consent)
    if since:
        rq, cq = rq.where(VetQuestion.created_at >= since), cq.where(VetQuestion.created_at >= since)
    waits = sorted((r - c).total_seconds() / 3600 for c, r in db.execute(rq).all())
    consent = dict(db.execute(cq).all())
    total = sum(consent.values())
    imported = db.execute(select(func.count()).select_from(ReviewItem).where(ReviewItem.origin == "consultation")).scalar()
    recheck = db.execute(select(func.count()).select_from(ReviewItem).where(
        ReviewItem.origin == "consultation", ReviewItem.needs_recheck.is_(True))).scalar()
    return {
        "days": days,
        "links": {k: links.get(k, 0) for k in ("requested", "accepted", "declined", "cancelled", "ended")},
        "questions": {k: questions.get(k, 0) for k in ("sent", "answered", "cancelled")},
        "reply_hours": {"count": len(waits), "median": round(statistics.median(waits), 1) if waits else None,
                        "max": round(waits[-1], 1) if waits else None},
        "consent": {"yes": consent.get(True, 0), "total": total, "rate": round(consent.get(True, 0) / total, 3) if total else None},
        "import": {"pending": len(pending(db)), "imported": imported, "needs_recheck": recheck},
    }


# ---------------------------------------------------------------- import queue
def pending(db: DB) -> list[dict]:
    """Consented + answered + live questions whose latest reply version has not been imported yet."""
    rows = db.execute(select(VetQuestion).where(VetQuestion.quality_use_consent.is_(True), VetQuestion.status == "answered",
                                                _alive_q()).order_by(VetQuestion.replied_at)).scalars().all()
    out = []
    for q in rows:
        rev = _latest_revision(db, q.id)
        if rev is None:
            continue
        done = db.execute(select(ReviewItem.id).where(ReviewItem.vet_question_id == q.id,
                                                      ReviewItem.vet_reply_revision_id == rev.id)).first()
        if done:
            continue
        earlier = db.execute(select(func.count()).select_from(ReviewItem).where(ReviewItem.vet_question_id == q.id)).scalar()
        out.append({"id": str(q.id), "replied_at": q.replied_at.isoformat() if q.replied_at else None,
                    "revision_id": str(rev.id), "corrected": rev.kind == "correction", "earlier_imports": earlier,
                    "question_preview": (q.snapshot.get("question") or "")[:80]})
    return out


def pending_detail(db: DB, qid: uuid.UUID) -> dict:
    q = _importable(db, qid, lock=False)
    rev = _latest_revision(db, q.id)
    s = q.snapshot
    dog = {k: v for k, v in (s.get("dog") or {}).items() if k not in ("name", "health_notes")}
    return {"id": str(q.id), "revision_id": str(rev.id), "corrected": rev.kind == "correction",
            "question": s.get("question", ""), "ai_answer": s.get("ai_answer", ""), "note_to_vet": s.get("note_to_vet"),
            "vet_reply": rev.body, "dog": dog, "sources": s.get("sources", []), "ai_response_type": s.get("ai_response_type")}


def _importable(db: DB, qid: uuid.UUID, lock: bool) -> VetQuestion:
    s = select(VetQuestion).where(VetQuestion.id == qid)
    q = db.execute(s.with_for_update() if lock else s).scalar_one_or_none()
    prof = db.get(DogProfile, q.profile_id) if q else None
    alive = prof is not None and (prof.expires_at is None or prof.expires_at > now())
    if q is None or not alive:
        raise ApiError(404, "not_found")
    if not q.quality_use_consent or q.status != "answered":
        raise ApiError(409, "not_importable", "품질 개선 동의와 수의사 회신이 있는 질문만 이관할 수 있어요.")
    return q


def _clean(text: str | None, field: str, required: bool = True) -> str | None:
    t = (text or "").strip()
    if not t:
        if required:
            raise ApiError(422, "invalid_input", extra={"fields": [field]})
        return None
    if len(t) > TEXT_MAX:
        raise ApiError(422, "invalid_input", extra={"fields": [field]})
    return t


def import_question(db: DB, operator: Reviewer, qid: uuid.UUID, *, revision_id: uuid.UUID, question: str,
                    ai_answer: str, note: str | None, vet_reply: str) -> dict:
    """Create the review copy from the operator's cleaned text. Re-checks everything under the question row lock."""
    from . import review as review_svc  # local import: review imports nothing from M03

    if operator.role != "operator":
        raise ApiError(403, "operator_only", "운영자만 이관할 수 있어요.")
    question, ai_answer = _clean(question, "question"), _clean(ai_answer, "ai_answer")
    vet_reply, note = _clean(vet_reply, "vet_reply"), _clean(note, "note_to_vet", required=False)
    q = _importable(db, qid, lock=True)  # same lock as withdrawal / deletion / correction
    rev = _latest_revision(db, q.id)
    if rev is None or rev.id != revision_id:
        raise ApiError(409, "source_changed", "수의사 회신이 바뀌었어요. 최신 내용으로 다시 정리해 주세요.")
    s = q.snapshot
    dog = {k: v for k, v in (s.get("dog") or {}).items() if k not in ("name", "health_notes")}
    history = [{"role": "note", "content": note}] if note else []
    item, created = review_svc.create_item(
        db, origin="consultation", source_ref=f"m03:{q.id}:{rev.id}", question=question, ai_answer=ai_answer,
        history=history, dog=dog, response_type=s.get("ai_response_type"),
        citations=[{"title": t} for t in s.get("sources", [])],
        versions={"source": "m03", "shared_at": s.get("shared_at", ""), "reply_version": rev.kind},
        link={"vet_question_id": q.id, "vet_reply_revision_id": rev.id, "vet_reference": vet_reply, "is_test": q.is_test})
    if not created:
        raise ApiError(409, "already_imported", "이미 이관한 회신 버전이에요.")
    db.add(ReviewEvent(item_id=item.id, actor_kind="operator", reviewer_id=operator.id, event="imported_from_m03",
                       detail={"reply_revision": str(rev.id), "reply_kind": rev.kind}))
    db.commit()
    return {"item_id": str(item.id), "revision_id": str(rev.id)}


# ---------------------------------------------------------------- consistency rules (called by M03 / profile code)
def flag_correction(db: DB, q: VetQuestion) -> int:
    """A reply corrected after import: flag the copies, keep their labels and recorded reply."""
    items = db.execute(select(ReviewItem).where(ReviewItem.vet_question_id == q.id)).scalars().all()
    for it in items:
        if not it.needs_recheck:
            it.needs_recheck = True
            db.add(ReviewEvent(item_id=it.id, actor_kind="system", event="source_corrected",
                               detail={"reply_revision_in_copy": str(it.vet_reply_revision_id) if it.vet_reply_revision_id else None}))
    return len(items)


def release(db: DB, question_ids: list[uuid.UUID], reason: str) -> dict:
    """Remove the review copies of these questions (withdrawal, deletion, expiry) without committing.

    Candidates made from a copy lose approval and are blocked; if one was published, its document leaves search
    first. The caller holds the question row lock (or deletes the profile in the same transaction)."""
    if not question_ids:
        return {"items": 0, "candidates": 0, "unpublished": 0}
    items = db.execute(select(ReviewItem).where(ReviewItem.vet_question_id.in_(question_ids))).scalars().all()
    if not items:
        return {"items": 0, "candidates": 0, "unpublished": 0}
    item_ids = [i.id for i in items]
    cands = db.execute(select(KnowledgeCandidate).where(KnowledgeCandidate.item_id.in_(item_ids))
                       .with_for_update()).scalars().all()
    unpublished = 0
    t = now()
    for c in cands:
        if c.publish_status == "active" and c.document_id is not None:
            load.dataset_lock(db, VET_DATASET_CODE)
            db.execute(update(Document).where(Document.id == c.document_id).values(status="inactive"))
            unpublished += 1
        was = c.publish_status
        if c.publish_status in ("approved", "active"):
            c.publish_status, c.approved_hash, c.approved_by, c.approved_at = "unapproved", None, None, None
        c.eval_result, c.eval_passed, c.source_withdrawn_at = None, None, t
        db.add(ReviewEvent(candidate_id=c.id, actor_kind="system", event="source_withdrawn",
                           detail={"reason": reason, "was": was, "document_inactivated": was == "active"}))
    for it in items:
        db.delete(it)  # labels, label concepts and item events go with it (FK cascade); candidates keep item_id NULL
    db.flush()
    return {"items": len(items), "candidates": len(cands), "unpublished": unpublished}


def release_for_profiles(db: DB, profile_ids: list[uuid.UUID], reason: str) -> dict:
    if not profile_ids:
        return {"items": 0, "candidates": 0, "unpublished": 0}
    qids = list(db.execute(select(VetQuestion.id).where(VetQuestion.profile_id.in_(profile_ids))
                           .with_for_update()).scalars())
    return release(db, qids, reason)


def is_live_copy(db: DB, item: ReviewItem) -> bool:
    """A consultation copy whose guardian profile expired is hidden before the cleanup job deletes it."""
    if item.origin != "consultation" or item.vet_question_id is None:
        return True
    q = db.get(VetQuestion, item.vet_question_id)
    prof = db.get(DogProfile, q.profile_id) if q else None
    return prof is not None and (prof.expires_at is None or prof.expires_at > now())


def live_copy_condition():
    """SQL: non-consultation items, or consultation items whose guardian profile is still alive."""
    alive = (select(VetQuestion.id).join(DogProfile, DogProfile.id == VetQuestion.profile_id)
             .where(VetQuestion.id == ReviewItem.vet_question_id)
             .where((DogProfile.expires_at.is_(None)) | (DogProfile.expires_at > now())).exists())
    return (ReviewItem.origin != "consultation") | alive
