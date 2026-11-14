"""M03 API (docs/04 §7-1). Guardian side under /api/v1 (vets, vet-links, vet-questions), vet side under /api/v1/vet,
and /api/v1/m03/availability, which always answers (screens decide what to show from it).

Every M03 route except availability answers 404 while M03_ENABLED is false.
"""
from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field

from ..db.models import DogProfile
from ..db.models_m03 import VetAccount
from ..services import m03
from ..services import profile as profile_svc
from .errors import ApiError
from .routes import PROFILE_COOKIE, DBDep, Guard, SessionDep, SettingsDep, settings_dep

VET_COOKIE = "tvc_vet"
VET_API = "/api/v1/vet"


def enabled(settings=Depends(settings_dep)) -> None:
    m03.require_enabled(settings)


def guardian_enabled(request: Request, db: DBDep, settings=Depends(settings_dep)) -> None:
    """'on': everyone; 'pilot': only this browser's profile if it opened the pilot link; 'off': nobody."""
    if not m03.guardian_allowed(settings, profile_svc.resolve(db, request.cookies.get(PROFILE_COOKIE))):
        raise ApiError(404, "not_found")


On = [Depends(enabled)]
public = APIRouter(prefix="/api/v1")
guardian = APIRouter(prefix="/api/v1", dependencies=[Depends(guardian_enabled)])
by_link = APIRouter(prefix="/api/v1", dependencies=On)  # confirmation link: no cookie; checked per question
vet = APIRouter(prefix=VET_API, dependencies=On)


@public.get("/m03/availability")
def availability(request: Request, db: DBDep, settings: SettingsDep):
    """Read by every app: enabled = this guardian can use it, vets = the vet screens are open, mode = switch state."""
    prof = profile_svc.resolve(db, request.cookies.get(PROFILE_COOKIE))
    m = m03.mode(settings)
    return {"enabled": m03.guardian_allowed(settings, prof), "vets": m != "off", "mode": m,
            "link_days": settings.vet_link_days}


class PilotIn(BaseModel):
    code: str = Field(min_length=1, max_length=200)


@public.post("/m03/pilot", dependencies=Guard)
def join_pilot(body: PilotIn, request: Request, db: DBDep, settings: SettingsDep):
    fwd = request.headers.get("x-forwarded-for", "")
    ip = fwd.split(",")[0].strip() or (request.client.host if request.client else "?")
    if not request.app.state.limiter.allow(uuid.uuid5(uuid.NAMESPACE_URL, "m03-pilot:" + ip), 5):
        raise ApiError(429, "rate_limited")
    m03.join_pilot(db, settings, profile_svc.resolve(db, request.cookies.get(PROFILE_COOKIE)), body.code)
    return {"enabled": True, "mode": "pilot"}


# ---------------------------------------------------------------- guardian
def current_profile(request: Request, db: DBDep) -> DogProfile:
    row = profile_svc.resolve(db, request.cookies.get(PROFILE_COOKIE))
    if row is None:
        raise ApiError(409, "profile_required")
    return row


ProfileDep = Annotated[DogProfile, Depends(current_profile)]


class LinkIn(BaseModel):
    vet_id: uuid.UUID


class QuestionIn(BaseModel):
    link_id: uuid.UUID
    answer_message_id: uuid.UUID
    note_to_vet: str | None = Field(default=None, max_length=m03.NOTE_MAX)
    shared_fields: list[str] = Field(default_factory=lambda: list(m03.DEFAULT_FIELDS), max_length=len(m03.SHAREABLE_FIELDS))
    quality_use_consent: bool = False


class ConsentIn(BaseModel):
    quality_use_consent: bool


@guardian.get("/vets")
def vets(db: DBDep, settings: SettingsDep):
    return {"items": m03.list_vets(db, settings)}


@guardian.get("/vet-links")
def links(prof: ProfileDep, db: DBDep, settings: SettingsDep):
    return {"items": m03.my_links(db, settings, prof)}


@guardian.post("/vet-links", status_code=201, dependencies=Guard)
def request_link(body: LinkIn, prof: ProfileDep, db: DBDep, settings: SettingsDep):
    return m03.request_link(db, settings, prof, body.vet_id)


@guardian.delete("/vet-links/{link_id}", dependencies=Guard)
def close_link(link_id: uuid.UUID, prof: ProfileDep, db: DBDep, settings: SettingsDep):
    """Cancel a pending request or end an accepted link (pending questions are cancelled)."""
    return m03.close_link(db, settings, prof, link_id)


@guardian.post("/vet-questions", status_code=201, dependencies=Guard)
def send_question(body: QuestionIn, prof: ProfileDep, session: SessionDep, db: DBDep, settings: SettingsDep):
    view, token = m03.send_question(db, settings, prof, session, link_id=body.link_id,
                                    answer_message_id=body.answer_message_id, note=body.note_to_vet,
                                    shared_fields=body.shared_fields, quality_use_consent=body.quality_use_consent)
    return {**view, "access_token": token}  # shown once; only its hash is stored


@guardian.get("/vet-questions")
def questions(prof: ProfileDep, db: DBDep, settings: SettingsDep):
    return {"items": m03.my_questions(db, settings, prof)}


@by_link.get("/vet-questions/by-token/{token}")
def question_by_token(token: str, db: DBDep, settings: SettingsDep):
    return m03.question_by_token(db, settings, token)


@guardian.get("/vet-questions/{qid}")
def question(qid: uuid.UUID, prof: ProfileDep, db: DBDep, settings: SettingsDep):
    return m03.question_detail(db, settings, prof, qid)


@guardian.post("/vet-questions/{qid}/cancel", dependencies=Guard)
def cancel_question(qid: uuid.UUID, prof: ProfileDep, db: DBDep, settings: SettingsDep):
    return m03.cancel_question(db, settings, prof, qid)


@guardian.patch("/vet-questions/{qid}", dependencies=Guard)
def consent(qid: uuid.UUID, body: ConsentIn, prof: ProfileDep, db: DBDep, settings: SettingsDep):
    return m03.set_consent(db, settings, prof, qid, body.quality_use_consent)


@guardian.post("/vet-questions/{qid}/link", dependencies=Guard)
def reissue(qid: uuid.UUID, prof: ProfileDep, db: DBDep, settings: SettingsDep):
    return {"access_token": m03.reissue_token(db, settings, prof, qid)}


@guardian.delete("/vet-questions/{qid}", status_code=204, dependencies=Guard)
def delete_question(qid: uuid.UUID, prof: ProfileDep, db: DBDep, settings: SettingsDep):
    m03.delete_question(db, settings, prof, qid)
    return Response(status_code=204)


# ---------------------------------------------------------------- vet
def current_vet(request: Request, db: DBDep) -> VetAccount:
    v = m03.vet_for_token(db, request.cookies.get(VET_COOKIE))
    if v is None:
        raise ApiError(401, "vet_login_required")
    return v


VetDep = Annotated[VetAccount, Depends(current_vet)]


class VetLoginIn(BaseModel):
    access_key: str = Field(min_length=8, max_length=200)


class ReplyIn(BaseModel):
    text: str | None = Field(default=None, max_length=m03.REPLY_MAX)


@vet.post("/login", dependencies=Guard)
def login(body: VetLoginIn, request: Request, response: Response, db: DBDep, settings: SettingsDep):
    fwd = request.headers.get("x-forwarded-for", "")
    ip = fwd.split(",")[0].strip() or (request.client.host if request.client else "?")
    if not request.app.state.limiter.allow(uuid.uuid5(uuid.NAMESPACE_URL, "vet-login:" + ip), settings.vet_logins_per_minute):
        raise ApiError(429, "rate_limited", "로그인 시도가 너무 많아요. 잠시 후 다시 시도해 주세요.")
    got = m03.vet_login(db, body.access_key, settings.vet_session_hours)
    if got is None:
        raise ApiError(401, "vet_login_failed")
    v, token = got
    response.set_cookie(VET_COOKIE, token, max_age=settings.vet_session_hours * 3600, httponly=True,
                        secure=settings.cookie_secure, samesite="strict", path=VET_API)
    return m03.vet_view(v)


@vet.post("/logout", dependencies=Guard)
def logout(request: Request, db: DBDep):
    m03.vet_logout(db, request.cookies.get(VET_COOKIE))
    out = Response(status_code=204)
    out.delete_cookie(VET_COOKIE, path=VET_API)
    return out


@vet.get("/me")
def me(v: VetDep):
    return m03.vet_view(v)


@vet.get("/links")
def vet_links(v: VetDep, db: DBDep):
    return {"items": m03.vet_links(db, v)}


@vet.post("/links/{link_id}/accept", dependencies=Guard)
def accept(link_id: uuid.UUID, v: VetDep, db: DBDep, settings: SettingsDep):
    return m03.decide_link(db, v, link_id, accept=True, days=settings.vet_link_days)


@vet.post("/links/{link_id}/decline", dependencies=Guard)
def decline(link_id: uuid.UUID, v: VetDep, db: DBDep):
    return m03.decide_link(db, v, link_id, accept=False)


@vet.post("/links/{link_id}/end", dependencies=Guard)
def end(link_id: uuid.UUID, v: VetDep, db: DBDep):
    return m03.vet_end_link(db, v, link_id)


@vet.get("/questions")
def vet_questions(v: VetDep, db: DBDep):
    return {"items": m03.vet_questions(db, v)}


@vet.get("/questions/{qid}")
def vet_question(qid: uuid.UUID, v: VetDep, db: DBDep):
    return m03.vet_question_detail(db, v, qid)


@vet.put("/questions/{qid}/draft", dependencies=Guard)
def draft(qid: uuid.UUID, body: ReplyIn, v: VetDep, db: DBDep):
    return m03.save_draft(db, v, qid, body.text)


@vet.post("/questions/{qid}/reply", dependencies=Guard)
def reply(qid: uuid.UUID, body: ReplyIn, v: VetDep, db: DBDep):
    return m03.send_reply(db, v, qid, body.text)


@vet.post("/questions/{qid}/correction", dependencies=Guard)
def correction(qid: uuid.UUID, body: ReplyIn, v: VetDep, db: DBDep):
    return m03.correct_reply(db, v, qid, body.text)
