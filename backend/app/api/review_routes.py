"""MVP1 review API (docs/04 §7): /api/v1/review/*. Reviewer cookie, separate from the guardian session.

Operator-only actions (reviewer creation, mock seeding, evaluation, activation) are scripts, not endpoints.
"""
from __future__ import annotations

import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from ..db.models_review import KnowledgeCandidate, ReviewItem, Reviewer
from ..domain import review as rules
from ..domain.review_guide import DISPUTE_TYPES, FIELDS, GUIDE_VERSION
from ..services import review as svc
from .errors import ApiError
from .routes import DBDep, Guard, SettingsDep

REVIEW_COOKIE = "tvc_reviewer"
router = APIRouter(prefix="/api/v1/review")


def current_reviewer(request: Request, db: DBDep) -> Reviewer:
    r = svc.reviewer_for_token(db, request.cookies.get(REVIEW_COOKIE))
    if r is None:
        raise ApiError(401, "review_login_required", "작업 화면에 다시 로그인해 주세요.")
    return r


ReviewerDep = Annotated[Reviewer, Depends(current_reviewer)]


def _raise(e: svc.ReviewError):
    raise ApiError(e.status, e.code, e.message, {"fields": e.fields} if e.fields else None)


# ------------------------------------------------------------------ bodies
Short = Annotated[str, StringConstraints(max_length=40)]


class ConceptTagIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: Annotated[str, StringConstraints(max_length=90)]
    assertion: Short


class LabelIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    accuracy: Short | None = None
    evidence_support: Short | None = None
    sufficiency: Short | None = None
    guidance: Short | None = None
    safety_issue: bool = False
    revised_answer: Annotated[str, StringConstraints(max_length=4000)] | None = None
    revision_reason: Annotated[str, StringConstraints(max_length=1000)] | None = None
    revision_evidence: list[Annotated[str, StringConstraints(max_length=300)]] = Field(default_factory=list, max_length=20)
    reuse_scope: Short | None = None
    note: Annotated[str, StringConstraints(max_length=1000)] | None = None
    concepts: list[ConceptTagIn] = Field(default_factory=list, max_length=40)
    active_seconds: int = Field(default=0, ge=0, le=3600)  # time spent since the last save


class LoginIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    access_key: Annotated[str, StringConstraints(strip_whitespace=True, min_length=10, max_length=100)]


class AdjudicateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dispute_type: Short
    note: Annotated[str, StringConstraints(max_length=1000)] | None = None


class CandidateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    item_id: uuid.UUID | None = None
    label_id: uuid.UUID | None = None
    kind: Literal["case", "general"]
    title: Annotated[str, StringConstraints(max_length=200)]
    content: Annotated[str, StringConstraints(max_length=4000)]
    conditions: dict = Field(default_factory=dict)
    evidence: list[Annotated[str, StringConstraints(max_length=300)]] = Field(default_factory=list, max_length=20)


def _label_data(body: LabelIn) -> tuple[dict, int]:
    d = body.model_dump(exclude={"active_seconds"})
    d["concepts"] = [c.model_dump() for c in body.concepts]
    return d, body.active_seconds


def _item(db, item_id: uuid.UUID) -> ReviewItem:
    from ..services.m03_hitl import is_live_copy
    item = db.get(ReviewItem, item_id)
    if item is None or not is_live_copy(db, item):  # expired guardian copies are hidden before cleanup
        raise ApiError(404, "not_found")
    return item


def _cand(db, cand_id: uuid.UUID) -> KnowledgeCandidate:
    c = db.get(KnowledgeCandidate, cand_id)
    if c is None:
        raise ApiError(404, "not_found")
    return c


# ------------------------------------------------------------------ auth
@router.post("/login", dependencies=Guard)
def login(body: LoginIn, request: Request, response: Response, db: DBDep, settings: SettingsDep):
    fwd = request.headers.get("x-forwarded-for", "")
    ip = fwd.split(",")[0].strip() or (request.client.host if request.client else "?")
    if not request.app.state.limiter.allow(uuid.uuid5(uuid.NAMESPACE_URL, "review-login:" + ip),
                                           settings.review_logins_per_minute):
        raise ApiError(429, "rate_limited", "로그인 시도가 너무 많아요. 잠시 후 다시 시도해 주세요.")
    got = svc.login(db, body.access_key, settings.reviewer_session_hours)
    if got is None:
        raise ApiError(401, "review_login_failed", "접근 키를 확인해 주세요.")
    reviewer, token = got
    response.set_cookie(REVIEW_COOKIE, token, max_age=settings.reviewer_session_hours * 3600, httponly=True,
                        secure=settings.cookie_secure, samesite="strict", path="/api/v1/review")
    return _me(reviewer, settings)


@router.post("/logout", dependencies=Guard)
def logout(request: Request, db: DBDep):
    svc.logout(db, request.cookies.get(REVIEW_COOKIE))
    out = Response(status_code=204)
    out.delete_cookie(REVIEW_COOKIE, path="/api/v1/review")
    return out


def _me(r: Reviewer, settings) -> dict:
    return {"id": str(r.id), "role": r.role, "display_name": r.display_name, "credential_status": r.credential_status,
            "production": settings.hitl_production}


@router.get("/me")
def me(reviewer: ReviewerDep, settings: SettingsDep):
    return _me(reviewer, settings)


# ------------------------------------------------------------------ guide, concepts
@router.get("/guide")
def guide(reviewer: ReviewerDep):
    return {"version": GUIDE_VERSION, "fields": FIELDS, "dispute_types": DISPUTE_TYPES}


@router.get("/concepts")
def concepts(reviewer: ReviewerDep, db: DBDep, scheme: str | None = None):
    idx = svc.concept_index(db)
    items = [{"code": k, "scheme": c.scheme, "label": c.label, "synonyms": list(c.synonyms), "status": c.status}
             for k, c in sorted(idx.items()) if (scheme is None or c.scheme == scheme) and c.status != "retired"]
    return {"items": items, "schemes": list(rules.CONCEPT_SCHEMES)}


# ------------------------------------------------------------------ items and labels
@router.get("/items")
def items(reviewer: ReviewerDep, db: DBDep, status: str | None = None, origin: str | None = None):
    return {"items": svc.list_items(db, reviewer, status, origin)}


@router.get("/items/{item_id}")
def item(item_id: uuid.UUID, reviewer: ReviewerDep, db: DBDep):
    return svc.item_detail(db, _item(db, item_id), reviewer)


@router.put("/items/{item_id}/label", dependencies=Guard)
def save(item_id: uuid.UUID, body: LabelIn, reviewer: ReviewerDep, db: DBDep):
    it = _item(db, item_id)
    data, secs = _label_data(body)
    try:
        svc.save_label(db, it, reviewer, data, secs)
    except svc.ReviewError as e:
        _raise(e)
    return svc.item_detail(db, it, reviewer)


@router.post("/items/{item_id}/label/submit", dependencies=Guard)
def submit(item_id: uuid.UUID, body: LabelIn, reviewer: ReviewerDep, db: DBDep):
    it = _item(db, item_id)
    data, secs = _label_data(body)
    try:
        svc.submit_label(db, it, reviewer, data, secs)
    except svc.ReviewError as e:
        db.rollback()
        _raise(e)
    return svc.item_detail(db, it, reviewer)


@router.post("/items/{item_id}/adjudicate", dependencies=Guard)
def adjudicate(item_id: uuid.UUID, body: AdjudicateIn, reviewer: ReviewerDep, db: DBDep):
    it = _item(db, item_id)
    try:
        svc.adjudicate(db, it, reviewer, body.dispute_type, body.note)
    except svc.ReviewError as e:
        _raise(e)
    return svc.item_detail(db, it, reviewer)


# ------------------------------------------------------------------ knowledge candidates
@router.get("/candidates")
def candidates(reviewer: ReviewerDep, db: DBDep):
    return {"items": svc.list_candidates(db)}


@router.post("/candidates", status_code=201, dependencies=Guard)
def create_candidate(body: CandidateIn, reviewer: ReviewerDep, db: DBDep):
    try:
        c = svc.create_candidate(db, reviewer, body.model_dump())
    except svc.ReviewError as e:
        _raise(e)
    return svc.candidate_view(c)


@router.put("/candidates/{cand_id}", dependencies=Guard)
def update_candidate(cand_id: uuid.UUID, body: CandidateIn, reviewer: ReviewerDep, db: DBDep):
    c = _cand(db, cand_id)
    try:
        svc.update_candidate(db, c, reviewer, body.model_dump())
    except svc.ReviewError as e:
        _raise(e)
    return svc.candidate_view(c)


@router.post("/candidates/{cand_id}/approve", dependencies=Guard)
def approve_candidate(cand_id: uuid.UUID, reviewer: ReviewerDep, db: DBDep, settings: SettingsDep):
    c = _cand(db, cand_id)
    try:
        svc.approve_candidate(db, c, reviewer, settings.hitl_production)
    except svc.ReviewError as e:
        _raise(e)
    return svc.candidate_view(c)


# ------------------------------------------------------------------ M03 operator: stats + import (docs/04 §7-1)
def m03_operator(reviewer: ReviewerDep) -> Reviewer:
    """HITL 통계 works whether or not M03 is open to guardians (numbers are simply zero while it is off)."""
    if reviewer.role != "operator":
        raise ApiError(403, "operator_only", "운영자만 볼 수 있어요.")
    return reviewer


OperatorDep = Annotated[Reviewer, Depends(m03_operator)]


class M03ImportIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision_id: uuid.UUID
    question: str = Field(max_length=4000)
    ai_answer: str = Field(max_length=4000)
    note_to_vet: str | None = Field(default=None, max_length=4000)
    vet_reply: str = Field(max_length=4000)


@router.get("/m03/stats")
def m03_stats(op: OperatorDep, db: DBDep, days: int | None = None):
    from ..services import m03_hitl
    if days is not None and not 1 <= days <= 3650:
        raise ApiError(422, "invalid_input", extra={"fields": ["days"]})
    return m03_hitl.stats(db, days)


@router.get("/m03/pending")
def m03_pending(op: OperatorDep, db: DBDep):
    from ..services import m03_hitl
    return {"items": m03_hitl.pending(db)}


@router.get("/m03/pending/{qid}")
def m03_pending_detail(qid: uuid.UUID, op: OperatorDep, db: DBDep):
    from ..services import m03_hitl
    return m03_hitl.pending_detail(db, qid)


@router.post("/m03/pending/{qid}/import", status_code=201, dependencies=Guard)
def m03_import(qid: uuid.UUID, body: M03ImportIn, op: OperatorDep, db: DBDep):
    from ..services import m03_hitl
    return m03_hitl.import_question(db, op, qid, revision_id=body.revision_id, question=body.question,
                                    ai_answer=body.ai_answer, note=body.note_to_vet, vet_reply=body.vet_reply)


# ------------------------------------------------------------------ 서비스 이용통계 (operator; Flow per day + Stock)
@router.get("/usage")
def usage_stats(op: OperatorDep, db: DBDep, settings: SettingsDep, days: int | None = None):
    from ..services import usage
    if days is not None and not 1 <= days <= 3650:
        raise ApiError(422, "invalid_input", extra={"fields": ["days"]})
    return usage.report(db, settings, days)
