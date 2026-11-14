"""MVP1 HITL services (docs/02 §4, docs/03 §4, docs/04 §7): reviewers, ontology seed, review bundles,
labels, status, disputes, knowledge candidates, regression gate and activation.

Rules live in domain/review.py; this module applies them to the database and writes review_event history.
Review records never enter RAG search; only activated candidates become documents.
"""
from __future__ import annotations

import hashlib
import json
import secrets
import uuid
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from typing import Sequence

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..db.models import Document
from ..db.models_review import (
    Concept,
    ConceptRelation,
    KnowledgeCandidate,
    LabelConcept,
    ReviewEvent,
    ReviewItem,
    ReviewLabel,
    Reviewer,
    ReviewerSession,
)
from ..domain import review as rules
from ..domain.ontology_seed import CONCEPTS, RELATIONS, SOURCE_NOTE
from ..domain.review_mock import MOCK_CANDIDATE, MOCK_CASES, MOCK_PREFIX
from ..ingest import load
from ..rag.embeddings import Embedder, cosine
from ..rag.retrieval import search

VET_DATASET_CODE = "vetreview"
VET_SOURCE_NAME = "TwinVet 수의사 검토 지식"
VET_USAGE_NOTE = "수의사 검토·독립 재검증 후 발행"


class ReviewError(Exception):
    """Business rule refusal. status: HTTP code the API maps it to."""

    def __init__(self, status: int, code: str, message: str, fields: dict | None = None):
        super().__init__(message)
        self.status, self.code, self.message, self.fields = status, code, message, fields or {}


def _sha(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _event(db: Session, *, actor: Reviewer | None, actor_kind: str | None = None, event: str,
           item: ReviewItem | None = None, candidate: KnowledgeCandidate | None = None, **detail) -> None:
    db.add(ReviewEvent(item_id=item.id if item else None, candidate_id=candidate.id if candidate else None,
                       actor_kind=actor_kind or (actor.role if actor else "system"),
                       reviewer_id=actor.id if actor else None, event=event, detail=detail))


# ---------------------------------------------------------------- reviewers and login
def create_reviewer(db: Session, role: str, display_name: str, email: str | None = None) -> tuple[Reviewer, str]:
    """Returns the reviewer and the raw access key (shown once; only its hash is stored)."""
    if role not in ("vet", "operator"):
        raise ValueError("role must be vet or operator")
    raw = "tvr_" + secrets.token_urlsafe(24)
    r = Reviewer(role=role, display_name=display_name.strip()[:40], email=email, access_key_hash=_sha(raw))
    db.add(r)
    db.commit()
    return r, raw


def login(db: Session, raw_key: str, hours: int) -> tuple[Reviewer, str] | None:
    r = db.execute(select(Reviewer).where(Reviewer.access_key_hash == _sha(raw_key.strip()))).scalar_one_or_none()
    if r is None or not r.active:
        return None
    token = secrets.token_urlsafe(32)
    db.add(ReviewerSession(reviewer_id=r.id, token_hash=_sha(token), expires_at=_now() + timedelta(hours=hours)))
    r.last_login_at = _now()
    db.commit()
    return r, token


def reviewer_for_token(db: Session, token: str | None) -> Reviewer | None:
    if not token:
        return None
    row = db.execute(
        select(Reviewer).join(ReviewerSession, ReviewerSession.reviewer_id == Reviewer.id)
        .where(ReviewerSession.token_hash == _sha(token), ReviewerSession.expires_at > _now(), Reviewer.active.is_(True))
    ).scalar_one_or_none()
    return row


def logout(db: Session, token: str | None) -> None:
    if token:
        db.execute(delete(ReviewerSession).where(ReviewerSession.token_hash == _sha(token)))
        db.commit()


# ---------------------------------------------------------------- ontology 0.1
def seed_ontology(db: Session) -> dict:
    """Insert missing draft concepts/relations. Never overwrites a concept a vet already approved or retired."""
    existing = {(c.scheme, c.code): c for c in db.execute(select(Concept)).scalars()}
    added = updated = 0
    for scheme, code, label, synonyms, definition in CONCEPTS:
        c = existing.get((scheme, code))
        if c is None:
            c = Concept(scheme=scheme, code=code, label=label, synonyms=synonyms, definition=definition,
                        status="draft", source_note=SOURCE_NOTE)
            db.add(c)
            existing[(scheme, code)] = c
            added += 1
        elif c.status == "draft" and (c.label, list(c.synonyms), c.definition) != (label, synonyms, definition):
            c.label, c.synonyms, c.definition = label, synonyms, definition
            updated += 1
    db.flush()
    have = {(r.subject_id, r.predicate, r.object_id) for r in db.execute(select(ConceptRelation)).scalars()}
    rel_added = 0
    for subj, pred, obj, cond in RELATIONS:
        s, o = existing[tuple(subj.split(":", 1))], existing[tuple(obj.split(":", 1))]
        if (s.id, pred, o.id) not in have:
            db.add(ConceptRelation(subject_id=s.id, predicate=pred, object_id=o.id, conditions=cond,
                                   source_note=SOURCE_NOTE, status="draft"))
            rel_added += 1
    db.commit()
    return {"concepts_added": added, "concepts_updated": updated, "relations_added": rel_added,
            "concepts_total": len(existing)}


def concept_index(db: Session) -> dict[str, Concept]:
    return {f"{c.scheme}:{c.code}": c for c in db.execute(select(Concept)).scalars()}


# ---------------------------------------------------------------- review bundles
def bundle_hash(question: str, history: list, dog: dict, ai_answer: str, citations: list, versions: dict) -> str:
    return _sha(json.dumps([question, history, dog, ai_answer, citations, versions], ensure_ascii=False, sort_keys=True))


def create_item(db: Session, *, origin: str, source_ref: str, question: str, ai_answer: str, history: list | None = None,
                dog: dict | None = None, response_type: str | None = None, citations: list | None = None,
                versions: dict | None = None, show_ai_suggestion: bool = True) -> tuple[ReviewItem, bool]:
    """Idempotent by (origin, source_ref). Returns (item, created)."""
    found = db.execute(select(ReviewItem).where(ReviewItem.origin == origin, ReviewItem.source_ref == source_ref)).scalar_one_or_none()
    if found:
        return found, False
    history, dog, citations, versions = history or [], dog or {}, citations or [], versions or {}
    item = ReviewItem(origin=origin, is_mock=origin == "mock", source_ref=source_ref, question=question, history=history,
                      dog=dog, ai_answer=ai_answer, response_type=response_type, citations=citations, versions=versions,
                      content_hash=bundle_hash(question, history, dog, ai_answer, citations, versions),
                      show_ai_suggestion=show_ai_suggestion)
    db.add(item)
    db.flush()
    _event(db, actor=None, event="item_created", item=item, origin=origin)
    return item, True


def put_ai_suggestion(db: Session, item: ReviewItem, data: dict, model: str) -> ReviewLabel:
    """AI pre-label (author_kind=ai). Stored as submitted; never changes review status."""
    lab = db.execute(select(ReviewLabel).where(ReviewLabel.item_id == item.id, ReviewLabel.author_kind == "ai")).scalar_one_or_none()
    if lab is None:
        lab = ReviewLabel(item_id=item.id, author_kind="ai")
        db.add(lab)
    _apply_label(lab, _label_input(data))
    lab.state, lab.submitted_at, lab.note = "submitted", _now(), f"model={model}"
    db.flush()
    _replace_concepts(db, lab, _label_input(data), concept_index(db))
    return lab


def create_mock_items(db: Session) -> dict:
    created = 0
    for case in MOCK_CASES:
        item, new = create_item(
            db, origin="mock", source_ref=case["ref"], question=MOCK_PREFIX + case["question"],
            ai_answer=MOCK_PREFIX + case["ai_answer"], history=case["history"], dog=case["dog"],
            response_type=case["response_type"], citations=case["citations"],
            versions={"model": "mock", "prompt_version": "mock", "index_version": "mock"},
            show_ai_suggestion=case["show_ai_suggestion"])
        if new:
            put_ai_suggestion(db, item, case["ai_label"], model="mock")
            created += 1
    db.commit()
    return {"mock_items_created": created, "mock_items_total": len(MOCK_CASES)}


# ---------------------------------------------------------------- labels
def _label_input(data: dict) -> rules.LabelInput:
    concepts = [rules.ConceptTag(c[0], c[1]) if isinstance(c, (list, tuple)) else rules.ConceptTag(c["code"], c["assertion"])
                for c in data.get("concepts") or []]
    return rules.LabelInput(
        accuracy=data.get("accuracy"), evidence_support=data.get("evidence_support"), sufficiency=data.get("sufficiency"),
        guidance=data.get("guidance"), safety_issue=bool(data.get("safety_issue")),
        revised_answer=(data.get("revised_answer") or None), revision_reason=(data.get("revision_reason") or None),
        revision_evidence=[str(x) for x in data.get("revision_evidence") or []], reuse_scope=data.get("reuse_scope"),
        note=(data.get("note") or None), concepts=concepts)


def _apply_label(lab: ReviewLabel, li: rules.LabelInput) -> None:
    for f in ("accuracy", "evidence_support", "sufficiency", "guidance", "safety_issue", "revised_answer",
              "revision_reason", "revision_evidence", "reuse_scope", "note"):
        setattr(lab, f, getattr(li, f))


def _replace_concepts(db: Session, lab: ReviewLabel, li: rules.LabelInput, index: dict[str, Concept]) -> None:
    db.execute(delete(LabelConcept).where(LabelConcept.label_id == lab.id))
    seen = set()
    for t in li.concepts:
        c = index.get(t.code)
        if c is not None and t.assertion in rules.ASSERTIONS and (c.id, t.assertion) not in seen:
            seen.add((c.id, t.assertion))
            db.add(LabelConcept(label_id=lab.id, concept_id=c.id, assertion=t.assertion))


def _my_label(db: Session, item: ReviewItem, reviewer: Reviewer) -> ReviewLabel | None:
    return db.execute(select(ReviewLabel).where(ReviewLabel.item_id == item.id, ReviewLabel.reviewer_id == reviewer.id)).scalar_one_or_none()


def save_label(db: Session, item: ReviewItem, reviewer: Reviewer, data: dict, active_seconds: int = 0) -> ReviewLabel:
    """Draft save (no checks besides allowed lengths). A submitted label cannot be changed."""
    lab = _my_label(db, item, reviewer)
    if lab is None:
        lab = ReviewLabel(item_id=item.id, author_kind=reviewer.role, reviewer_id=reviewer.id,
                          ai_suggestion_shown=item.show_ai_suggestion)
        db.add(lab)
    elif lab.state == "submitted":
        raise ReviewError(409, "label_submitted", "제출한 라벨은 수정할 수 없습니다")
    li = _label_input(data)
    _apply_label(lab, li)
    lab.active_seconds = (lab.active_seconds or 0) + max(0, min(int(active_seconds or 0), 3600))
    db.flush()
    _replace_concepts(db, lab, li, concept_index(db))
    db.commit()
    return lab


def submit_label(db: Session, item: ReviewItem, reviewer: Reviewer, data: dict, active_seconds: int = 0) -> ReviewLabel:
    index = concept_index(db)
    errors = rules.check_label(_label_input(data), {k: c.status for k, c in index.items()})
    if errors:
        raise ReviewError(422, "label_invalid", "입력을 확인해 주세요", errors)
    lab = save_label(db, item, reviewer, data, active_seconds)
    lab.state, lab.submitted_at = "submitted", _now()
    _event(db, actor=reviewer, event="label_submitted", item=item, label_id=str(lab.id),
           author_kind=lab.author_kind, ai_suggestion_shown=lab.ai_suggestion_shown)
    recompute_status(db, item, actor=reviewer)
    db.commit()
    return lab


def submitted_labels(db: Session, item: ReviewItem) -> list[ReviewLabel]:
    return list(db.execute(select(ReviewLabel).where(ReviewLabel.item_id == item.id, ReviewLabel.state == "submitted")
                           .order_by(ReviewLabel.submitted_at)).scalars())


def _as_submitted(labs: Sequence[ReviewLabel]) -> list[rules.SubmittedLabel]:
    return [rules.SubmittedLabel(l.author_kind, str(l.reviewer_id) if l.reviewer_id else None, l.accuracy or "",
                                 l.guidance or "", l.reuse_scope or "", l.evidence_support or "", l.sufficiency or "",
                                 bool(l.safety_issue)) for l in labs]


def recompute_status(db: Session, item: ReviewItem, actor: Reviewer | None = None) -> str:
    new = rules.review_status(_as_submitted(submitted_labels(db, item)))
    if new != item.review_status:
        _event(db, actor=actor, event="status_changed", item=item, **{"from": item.review_status, "to": new})
        item.review_status = new
        if new != "disputed":
            item.dispute_type = None
    return new


def adjudicate(db: Session, item: ReviewItem, reviewer: Reviewer, dispute_type: str, note: str | None) -> ReviewItem:
    if item.review_status != "disputed":
        raise ReviewError(409, "not_disputed", "이견 상태인 사례만 조정 기록을 남길 수 있습니다")
    if dispute_type not in rules.DISPUTE_TYPES:
        raise ReviewError(422, "dispute_type_invalid", "이견 유형을 골라 주세요", {"dispute_type": "허용되지 않는 값입니다"})
    item.dispute_type = dispute_type
    _event(db, actor=reviewer, event="dispute_recorded", item=item, dispute_type=dispute_type, note=(note or "")[:1000])
    db.commit()
    return item


# ---------------------------------------------------------------- views (visibility rules)
def label_view(lab: ReviewLabel, concepts_by_id: dict) -> dict:
    tags = []
    return {
        "id": str(lab.id), "author_kind": lab.author_kind, "reviewer_id": str(lab.reviewer_id) if lab.reviewer_id else None,
        "state": lab.state, "ai_suggestion_shown": lab.ai_suggestion_shown,
        **{f: getattr(lab, f) for f in ("accuracy", "evidence_support", "sufficiency", "guidance", "safety_issue",
                                         "revised_answer", "revision_reason", "revision_evidence", "reuse_scope", "note")},
        "concepts": tags, "submitted_at": lab.submitted_at.isoformat() if lab.submitted_at else None,
        "active_seconds": lab.active_seconds,
    }


def _label_concepts(db: Session, label_ids: list) -> dict:
    out: dict = {}
    if not label_ids:
        return out
    rows = db.execute(select(LabelConcept.label_id, Concept.scheme, Concept.code, LabelConcept.assertion)
                      .join(Concept, Concept.id == LabelConcept.concept_id).where(LabelConcept.label_id.in_(label_ids)))
    for lid, scheme, code, assertion in rows:
        out.setdefault(lid, []).append({"code": f"{scheme}:{code}", "assertion": assertion})
    return out


def item_summary(item: ReviewItem, mine: ReviewLabel | None) -> dict:
    return {"id": str(item.id), "origin": item.origin, "is_mock": item.is_mock, "question": item.question[:120],
            "review_status": item.review_status, "dispute_type": item.dispute_type,
            "my_label": mine.state if mine else None, "created_at": item.created_at.isoformat()}


def list_items(db: Session, reviewer: Reviewer, status: str | None = None, origin: str | None = None) -> list[dict]:
    q = select(ReviewItem).order_by(ReviewItem.created_at, ReviewItem.source_ref)
    if status:
        q = q.where(ReviewItem.review_status == status)
    if origin:
        q = q.where(ReviewItem.origin == origin)
    items = list(db.execute(q).scalars())
    mine = {l.item_id: l for l in db.execute(select(ReviewLabel).where(ReviewLabel.reviewer_id == reviewer.id)).scalars()}
    return [item_summary(i, mine.get(i.id)) for i in items]


def record_auto_check(db: Session, item: ReviewItem, check: dict) -> None:
    """Automatic answer check (AI). History only; never changes review status or labels."""
    _event(db, actor=None, actor_kind="ai", event="auto_checked", item=item, **check)


def latest_auto_check(db: Session, item: ReviewItem) -> dict | None:
    ev = db.execute(select(ReviewEvent).where(ReviewEvent.item_id == item.id, ReviewEvent.event == "auto_checked")
                    .order_by(ReviewEvent.created_at.desc()).limit(1)).scalar_one_or_none()
    return ev.detail if ev else None


def item_detail(db: Session, item: ReviewItem, reviewer: Reviewer) -> dict:
    labels = list(db.execute(select(ReviewLabel).where(ReviewLabel.item_id == item.id)).scalars())
    tags = _label_concepts(db, [l.id for l in labels])

    def view(l: ReviewLabel) -> dict:
        v = label_view(l, {})
        v["concepts"] = tags.get(l.id, [])
        return v

    mine = next((l for l in labels if l.reviewer_id == reviewer.id), None)
    ai = next((l for l in labels if l.author_kind == "ai"), None)
    submitted = mine is not None and mine.state == "submitted"
    others = [view(l) for l in labels if l.state == "submitted" and l.author_kind != "ai" and l is not mine] if submitted else []
    return {
        "item": {
            **item_summary(item, mine), "question": item.question, "history": item.history, "dog": item.dog,
            "ai_answer": item.ai_answer, "response_type": item.response_type, "citations": item.citations,
            "versions": item.versions, "show_ai_suggestion": item.show_ai_suggestion,
        },
        "my_label": view(mine) if mine else None,
        # AI suggestion: only on items chosen to show it, or after my own submit (for comparison)
        "ai_suggestion": view(ai) if ai and (item.show_ai_suggestion or submitted) else None,
        "other_labels": others,  # hidden until my label is submitted (independent labeling)
        "other_label_count": sum(1 for l in labels if l.state == "submitted" and l.author_kind != "ai" and l is not mine),
        # technical check against the retrieved sources (AI); not a label and not veterinary verification
        "auto_check": latest_auto_check(db, item),
    }


# ---------------------------------------------------------------- knowledge candidates
def candidate_hash(kind: str, title: str, content: str, conditions: dict, evidence: list) -> str:
    return _sha(json.dumps([kind, title, content, conditions, evidence], ensure_ascii=False, sort_keys=True))


def _validate_candidate(data: dict) -> dict:
    errors = {}
    if data.get("kind") not in ("case", "general"):
        errors["kind"] = "사례 또는 일반 지식을 골라 주세요"
    title, content = (data.get("title") or "").strip(), (data.get("content") or "").strip()
    if not 2 <= len(title) <= 200:
        errors["title"] = "제목은 2~200자입니다"
    if not 10 <= len(content) <= 4000:
        errors["content"] = "내용은 10~4000자입니다"
    if not data.get("evidence"):
        errors["evidence"] = "근거를 하나 이상 입력해 주세요"
    if errors:
        raise ReviewError(422, "candidate_invalid", "입력을 확인해 주세요", errors)
    return {"kind": data["kind"], "title": title, "content": content, "conditions": data.get("conditions") or {},
            "evidence": [str(x) for x in data.get("evidence") or []]}


def create_candidate(db: Session, reviewer: Reviewer, data: dict) -> KnowledgeCandidate:
    v = _validate_candidate(data)
    item = db.get(ReviewItem, uuid.UUID(str(data["item_id"]))) if data.get("item_id") else None
    if data.get("item_id") and item is None:
        raise ReviewError(404, "item_not_found", "검토 묶음을 찾을 수 없습니다")
    is_mock = bool(item and item.is_mock)
    if is_mock and not v["title"].startswith(MOCK_PREFIX):
        v["title"] = MOCK_PREFIX + v["title"]
    cand = KnowledgeCandidate(item_id=item.id if item else None, label_id=data.get("label_id"), **v,
                              content_hash=candidate_hash(**v), is_mock=is_mock, created_by=reviewer.id)
    db.add(cand)
    db.flush()
    _event(db, actor=reviewer, event="candidate_created", candidate=cand, item=item)
    db.commit()
    return cand


def update_candidate(db: Session, cand: KnowledgeCandidate, reviewer: Reviewer, data: dict) -> KnowledgeCandidate:
    if cand.publish_status in ("rejected", "retired"):
        raise ReviewError(409, "candidate_closed", "종료된 후보는 수정할 수 없습니다")
    v = _validate_candidate({**data, "kind": data.get("kind", cand.kind)})
    if cand.is_mock and not v["title"].startswith(MOCK_PREFIX):
        v["title"] = MOCK_PREFIX + v["title"]
    new_hash = candidate_hash(**v)
    for k, val in v.items():
        setattr(cand, k, val)
    if new_hash != cand.content_hash:
        cand.content_hash = new_hash
        if cand.publish_status in ("approved", "active"):
            # approval binds to the exact content: an edit needs a new approval and a new evaluation.
            # An active document stays as published until the new version is activated.
            cand.publish_status, cand.approved_hash, cand.approved_by, cand.approved_at = "unapproved", None, None, None
            _event(db, actor=reviewer, event="approval_cleared", candidate=cand, reason="content_changed")
        cand.eval_result, cand.eval_passed = None, None
    _event(db, actor=reviewer, event="candidate_updated", candidate=cand)
    db.commit()
    return cand


def _reuse_scope_ok(db: Session, cand: KnowledgeCandidate) -> bool:
    ok = ("conditional", "general_candidate")
    if cand.label_id:
        lab = db.get(ReviewLabel, cand.label_id)
        return bool(lab and lab.reuse_scope in ok)
    if cand.item_id is None:
        return False
    return any(l.author_kind == "vet" and l.reuse_scope in ok for l in submitted_labels(db, db.get(ReviewItem, cand.item_id)))


def candidate_authors(db: Session, cand: KnowledgeCandidate) -> set[str]:
    """Everyone who wrote or edited the candidate (creator + candidate_updated events)."""
    ids = {str(cand.created_by)} if cand.created_by else set()
    rows = db.execute(select(ReviewEvent.reviewer_id).where(
        ReviewEvent.candidate_id == cand.id, ReviewEvent.event.in_(("candidate_created", "candidate_updated")),
        ReviewEvent.reviewer_id.is_not(None))).scalars()
    return ids | {str(r) for r in rows}


def approve_candidate(db: Session, cand: KnowledgeCandidate, reviewer: Reviewer, production: bool) -> KnowledgeCandidate:
    if cand.publish_status not in ("unapproved",):
        raise ReviewError(409, "candidate_not_unapproved", "승인 대기 상태의 후보만 승인할 수 있습니다")
    item = db.get(ReviewItem, cand.item_id) if cand.item_id else None
    reason = rules.approval_block_reason(
        approver_role=reviewer.role, approver_id=str(reviewer.id), production=production, is_mock=cand.is_mock,
        item_status=item.review_status if item else None, reuse_scope_ok=_reuse_scope_ok(db, cand),
        candidate_authors=candidate_authors(db, cand))
    if reason:
        raise ReviewError(409, "approval_blocked", reason)
    cand.publish_status, cand.approved_hash, cand.approved_by, cand.approved_at = "approved", cand.content_hash, reviewer.id, _now()
    _event(db, actor=reviewer, event="candidate_approved", candidate=cand, content_hash=cand.content_hash)
    db.commit()
    return cand


def candidate_view(c: KnowledgeCandidate) -> dict:
    return {"id": str(c.id), "item_id": str(c.item_id) if c.item_id else None, "kind": c.kind, "title": c.title,
            "content": c.content, "conditions": c.conditions, "evidence": c.evidence, "is_mock": c.is_mock,
            "publish_status": c.publish_status, "approved": c.approved_hash is not None and c.approved_hash == c.content_hash,
            "approved_at": c.approved_at.isoformat() if c.approved_at else None, "eval_result": c.eval_result,
            "eval_passed": c.eval_passed, "document_id": str(c.document_id) if c.document_id else None,
            "updated_at": c.updated_at.isoformat() if c.updated_at else None}


def list_candidates(db: Session) -> list[dict]:
    return [candidate_view(c) for c in db.execute(select(KnowledgeCandidate).order_by(KnowledgeCandidate.created_at)).scalars()]


# ---------------------------------------------------------------- regression gate and activation
CONDITION_LABELS = {"breed_size": "견 크기", "breeds": "품종", "life_stages": "생애 단계", "sex": "성별",
                    "neutered": "중성화", "situation": "상황", "exclusions": "적용 제외"}
LIFE_STAGE_KO = {"puppy": "어린 강아지", "adult": "성견", "senior": "노령견"}


def conditions_text(conditions: dict | None) -> str:
    """'적용 조건: 견 크기 소형견; 생애 단계 노령견. ...' — empty when there are no conditions."""
    parts = []
    for k, v in (conditions or {}).items():
        vals = v if isinstance(v, list) else [v]
        vals = [LIFE_STAGE_KO.get(str(x), str(x)) if k == "life_stages" else str(x) for x in vals if str(x).strip()]
        if vals:
            parts.append(f"{CONDITION_LABELS.get(k, k)} {', '.join(vals)}")
    return f"적용 조건: {'; '.join(parts)}. 이 조건에 해당할 때만 적용한다." if parts else ""


def knowledge_body(c: KnowledgeCandidate) -> str:
    """Published chunk text. The application conditions travel inside the text, so the answer model sees them in the
    source block (search results carry no other structured field)."""
    cond = conditions_text(c.conditions)
    return f"{c.content}\n{cond}" if cond else c.content


def _doc_text(c: KnowledgeCandidate) -> str:
    return load.embedding_text("vet_knowledge", c.title, None, None, knowledge_body(c))


def evaluate_candidate(db: Session, cand: KnowledgeCandidate, embedder: Embedder, questions: Sequence[str],
                       *, top_k: int, max_spillover: float, min_questions: int = 30) -> dict:
    """Search-impact check before activation (dev/regression only, docs/01 §7). NOT a quality or safety verdict:
    displacing old results can be an improvement, and a wrong candidate that is rarely retrieved scores low.

    self_found: a query made of the candidate title would rank the candidate within the current top_k.
    spillover: share of comparable questions (current top_k full) whose top_k this candidate would displace.
    Validity (real candidates only): at least min_questions questions, at least min_questions comparable, and a
    full top_k for the title query; otherwise the result is invalid and never passes. Mock candidates run as a
    flow test (kind=search_impact(mock_flow)) without the validity rule; that result never counts for real knowledge.
    """
    vecs = embedder.embed([_doc_text(cand), cand.title, *questions])
    cvec, tvec, qvecs = vecs[0], vecs[1], vecs[2:]

    def kth(vec) -> float | None:
        """Score of the current top_k-th hit; None when fewer than top_k hits exist (nothing to compare)."""
        hits = search(db, vec, model=embedder.model, life_stage=None, top_k=top_k, allow_mock=cand.is_mock)
        return hits[-1].score if len(hits) >= top_k else None

    t_k = kth(tvec)
    self_found = t_k is None or cosine(tvec, cvec) >= t_k
    displaced = comparable = 0
    for q in qvecs:
        q_k = kth(q)
        if q_k is not None:
            comparable += 1
            displaced += int(cosine(q, cvec) > q_k)
    rate = displaced / comparable if comparable else None
    invalid = None
    if not cand.is_mock:
        if len(qvecs) < min_questions:
            invalid = f"점검 질문이 {len(qvecs)}개로 최소 {min_questions}개보다 적습니다"
        elif comparable < min_questions:
            invalid = f"비교 가능한 질문이 {comparable}개뿐입니다(같은 임베딩 모델의 색인 문서 부족)"
        elif t_k is None:
            invalid = "제목 검색 결과가 상위 개수보다 적어 비교할 수 없습니다"
    passed = invalid is None and bool(self_found) and (rate or 0.0) <= max_spillover
    result = {"kind": "search_impact(mock_flow)" if cand.is_mock else "search_impact", "valid": invalid is None,
              "invalid_reason": invalid, "model": embedder.model, "questions": len(qvecs), "comparable": comparable,
              "top_k": top_k, "self_found": bool(self_found), "spillover": round(rate, 4) if rate is not None else None,
              "max_spillover": max_spillover, "evaluated_hash": cand.content_hash, "at": _now().isoformat(),
              "note": "검색 영향 점검 - 품질·안전 합격 판정이 아님"}
    cand.eval_result, cand.eval_passed = result, passed
    _event(db, actor=None, event="candidate_evaluated", candidate=cand, passed=passed, valid=invalid is None,
           spillover=result["spillover"])
    db.commit()
    return {**result, "passed": passed}


def activate_candidate(db: Session, cand: KnowledgeCandidate, embedder: Embedder, production: bool,
                       actor: Reviewer | None = None) -> dict:
    ev = cand.eval_result or {}
    # a mock-flow result never counts for real knowledge
    eval_ok = (bool(cand.eval_passed) and ev.get("evaluated_hash") == cand.content_hash
               and ev.get("model") == embedder.model and bool(ev.get("valid"))
               and (cand.is_mock or ev.get("kind") == "search_impact"))
    reason = rules.activation_block_reason(publish_status=cand.publish_status, approved_hash=cand.approved_hash,
                                           content_hash=cand.content_hash, eval_passed=eval_ok, is_mock=cand.is_mock,
                                           production=production)
    if reason:
        raise ReviewError(409, "activation_blocked", reason)
    source_id = f"candidate-{cand.id}"
    doc = db.execute(select(Document).where(Document.dataset_code == VET_DATASET_CODE,
                                            Document.source_record_id == source_id)).scalar_one_or_none()
    life_stages = [s for s in (cand.conditions or {}).get("life_stages", []) if s in ("puppy", "adult", "senior")]
    if doc is None:
        doc = Document(dataset_code=VET_DATASET_CODE, source_record_id=source_id, document_type="vet_knowledge",
                       title=cand.title, life_stages=life_stages, source_name=VET_SOURCE_NAME, usage_note=VET_USAGE_NOTE,
                       meta={"candidate_id": str(cand.id), "is_mock": cand.is_mock, "conditions": cand.conditions})
        db.add(doc)
        db.commit()
    body = knowledge_body(cand)
    run = load.start_run(db, VET_DATASET_CODE, "hitl", embedder.model)
    try:
        d = load.DocInput(source_record_id=source_id, document_type="vet_knowledge", title=cand.title, department=None,
                          disease_names=[], life_stages=life_stages, content_hash=cand.content_hash,
                          storage_ref=f"knowledge_candidate/{cand.id}",
                          meta={"candidate_id": str(cand.id), "is_mock": cand.is_mock, "conditions": cand.conditions},
                          chunks=[{"ordinal": 0, "content": body, "content_hash": _sha(body),
                                   "tokens": max(1, len(body) // 2), "locator": None}])
        load.stage(db, run, [d])
        load.embed_pending(db, run, embedder)
        v = load.validate(db, run, embedder.model, sample=1)
        if not v["ok"]:
            load.fail_run(db, run, "validation_failed")
            raise ReviewError(409, "activation_failed", "색인 검증에 실패해 활성화하지 않았습니다")
        act = load.activate(db, run)
    except ReviewError:
        raise
    except Exception:
        load.fail_run(db, run, "activation_error")
        raise
    cand.publish_status, cand.document_id = "active", doc.id
    _event(db, actor=actor, event="candidate_activated", candidate=cand, document_id=str(doc.id),
           content_hash=cand.content_hash, model=embedder.model)
    db.commit()
    return {"activated": act["activated"], "document_id": str(doc.id), "index_version": load.index_version(VET_DATASET_CODE, "hitl", embedder.model)}


def create_mock_candidate(db: Session, reviewer: Reviewer) -> KnowledgeCandidate:
    item = db.execute(select(ReviewItem).where(ReviewItem.origin == "mock", ReviewItem.source_ref == MOCK_CANDIDATE["ref"])).scalar_one()
    return create_candidate(db, reviewer, {**{k: v for k, v in MOCK_CANDIDATE.items() if k != "ref"}, "item_id": item.id})


def counts(db: Session) -> dict:
    from sqlalchemy import func

    return {
        "items": dict(db.execute(select(ReviewItem.review_status, func.count()).group_by(ReviewItem.review_status)).all()),
        "labels": dict(db.execute(select(ReviewLabel.author_kind, func.count()).where(ReviewLabel.state == "submitted")
                                  .group_by(ReviewLabel.author_kind)).all()),
        "candidates": dict(db.execute(select(KnowledgeCandidate.publish_status, func.count())
                                      .group_by(KnowledgeCandidate.publish_status)).all()),
        "reviewers": dict(db.execute(select(Reviewer.role, func.count()).where(Reviewer.active.is_(True)).group_by(Reviewer.role)).all()),
    }
