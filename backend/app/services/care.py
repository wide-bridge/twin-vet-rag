"""Care (P09): the consultation record as one card per consultation (docs/05 §3-1).

Cards are listed from the consultations still kept (7 days). The AI summary/topic is made on first view and
stored on the consultation; it is made again when the conversation grew. Grouping (same KST day + same topic)
and the "show to the hospital" page are done in the browser from these cards.
"""
from __future__ import annotations

import uuid
from collections import defaultdict

from sqlalchemy import func, select
from sqlalchemy.orm import Session as DB

from ..api.errors import ApiError
from ..config import Settings
from ..db.models import AnswerRun, Consultation, Message
from ..db.models import Session as SessionRow
from ..rag.llm import LLM, LLMError
from ..rag.prompts import SUMMARY_SCHEMA, SUMMARY_SYSTEM, TOPICS, DogInfo, summary_user
from .consult import dog_view, now, own_consultation


def _tags(c: Consultation, types: list[str], busy: bool) -> list[str]:
    tags = []
    if c.vet_advised:
        tags.append("vet_advised")
    if busy:
        tags.append("in_progress")
    elif types and types[-1] == "clarification":
        tags.append("clarifying")
    if types and "grounded" not in types:
        if "insufficient_evidence" in types:
            tags.append("insufficient")
        elif all(t == "out_of_scope" for t in types):
            tags.append("out_of_scope")
    return tags


def card(c: Consultation, first_q: str, count: int, types: list[str], busy: bool) -> dict:
    return {"id": str(c.id), "created_at": c.created_at.isoformat(), "dog": dog_view(c),
            "first_question": first_q[:200], "message_count": count,
            "topic": c.topic, "topics": c.topics or [], "summary": c.summary, "vet_advised": c.vet_advised,
            "summary_fresh": c.summary_turns == count and not busy,
            "tags": _tags(c, types, busy)}


def list_cards(db: DB, session: SessionRow) -> list[dict]:
    cons = db.execute(select(Consultation).where(Consultation.session_id == session.id)
                      .order_by(Consultation.created_at.desc())).scalars().all()
    if not cons:
        return []
    ids = [c.id for c in cons]
    counts = dict(db.execute(select(Message.consultation_id, func.count()).where(Message.consultation_id.in_(ids))
                             .group_by(Message.consultation_id)).all())
    firsts = {}
    for cid, content in db.execute(select(Message.consultation_id, Message.content)
                                   .where(Message.consultation_id.in_(ids), Message.role == "user")
                                   .order_by(Message.consultation_id, Message.sequence)).all():
        firsts.setdefault(cid, content)
    types: dict[uuid.UUID, list[str]] = defaultdict(list)
    busy: set[uuid.UUID] = set()
    for cid, status, rtype in db.execute(select(AnswerRun.consultation_id, AnswerRun.status, AnswerRun.response_type)
                                         .where(AnswerRun.consultation_id.in_(ids)).order_by(AnswerRun.created_at)).all():
        if status == "completed":
            types[cid].append(rtype)
        elif status in ("queued", "running"):
            busy.add(cid)
    # a consultation with no question yet (created, then abandoned) has nothing to show
    return [card(c, firsts[c.id], counts.get(c.id, 0), types[c.id], c.id in busy) for c in cons if c.id in firsts]


def summarize(db: DB, settings: Settings, llm: LLM | None, session: SessionRow, consultation_id: uuid.UUID) -> dict:
    c = own_consultation(db, session, consultation_id)
    msgs = db.execute(select(Message).where(Message.consultation_id == c.id).order_by(Message.sequence)).scalars().all()
    runs = db.execute(select(AnswerRun.status, AnswerRun.response_type).where(AnswerRun.consultation_id == c.id)
                      .order_by(AnswerRun.created_at)).all()
    types = [r.response_type for r in runs if r.status == "completed"]
    busy = any(r.status in ("queued", "running") for r in runs)
    first = next((m.content for m in msgs if m.role == "user"), "")
    if not msgs:
        raise ApiError(404, "not_found")
    if c.summary_turns == len(msgs) and not busy:
        return card(c, first, len(msgs), types, busy)
    if llm is None:
        raise ApiError(503, "summary_unavailable")
    dog = DogInfo(c.dog_breed, c.dog_age_months, float(c.dog_weight_kg) if c.dog_weight_kg is not None else None,
                  c.dog_neutered, c.dog_health_notes, c.dog_age_basis, c.dog_name)
    try:
        r = llm.complete_json(model=settings.llm_classifier_model, system=SUMMARY_SYSTEM,
                              user=summary_user(dog, [(m.role, m.content) for m in msgs]),
                              schema=SUMMARY_SCHEMA, name="care_summary", reasoning="low")
    except LLMError:
        raise ApiError(503, "summary_unavailable")
    d = r.data
    topic = d.get("topic") if d.get("topic") in TOPICS else "other"
    topics = [topic] + [t for t in d.get("topics", []) if t in TOPICS and t != topic]
    c.topic, c.topics = topic, list(dict.fromkeys(topics))[:3]
    c.summary = (d.get("summary") or "").strip()[:300] or None
    c.vet_advised = bool(d.get("vet_advised"))
    c.summary_turns, c.summary_at = len(msgs), now()
    db.commit()
    return card(c, first, len(msgs), types, busy)
