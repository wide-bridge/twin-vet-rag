"""PoC API (docs/04 §2). All paths under /api/v1."""
from __future__ import annotations

import uuid
from typing import Annotated, Iterator

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from sqlalchemy.orm import Session as DB

from ..config import Settings
from ..db.models import Session as SessionRow
from ..domain.dog import DogProfileIn
from ..services import consult
from .errors import ApiError

COOKIE = "tvc_session"
CLIENT_HEADER = "x-twinvet-client"

router = APIRouter(prefix="/api/v1")


# ------------------------------------------------------------------ dependencies
def settings_dep(request: Request) -> Settings:
    return request.app.state.settings


def db_dep(request: Request) -> Iterator[DB]:
    with request.app.state.Session() as db:
        yield db


def same_origin(request: Request, settings: Settings = Depends(settings_dep)) -> None:
    """CSRF guard for state-changing requests: a custom header (cross-site forms cannot send it and
    cross-origin scripts need a CORS preflight, which is not enabled) plus an Origin allow-list."""
    if request.headers.get(CLIENT_HEADER) != "web":
        raise ApiError(403, "forbidden_origin")
    origin = request.headers.get("origin")
    if origin:
        own = f"{request.url.scheme}://{request.url.netloc}"
        if origin.rstrip("/") not in settings.origins and origin.rstrip("/") != own:
            raise ApiError(403, "forbidden_origin")


def current_session(request: Request, db: DB = Depends(db_dep)) -> SessionRow:
    s = consult.resolve_session(db, request.cookies.get(COOKIE))
    if s is None:
        raise ApiError(401, "session_expired")
    return s


SessionDep = Annotated[SessionRow, Depends(current_session)]
DBDep = Annotated[DB, Depends(db_dep)]
SettingsDep = Annotated[Settings, Depends(settings_dep)]
Guard = [Depends(same_origin)]


# ------------------------------------------------------------------ bodies
class ConsultationIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dog: DogProfileIn


class QuestionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: Annotated[str, StringConstraints(max_length=20000)]
    client_request_id: Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]{8,64}$")]


class FeedbackIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    helpful: bool
    comment: Annotated[str, StringConstraints(strip_whitespace=True, max_length=300)] | None = None


# ------------------------------------------------------------------ P01 session / consultation
@router.post("/sessions", status_code=201, dependencies=Guard)
def create_session(request: Request, response: Response, db: DBDep, settings: SettingsDep):
    existing = consult.resolve_session(db, request.cookies.get(COOKIE))
    if existing is not None:  # keep the browser's session (its consultations stay reachable)
        response.status_code = 200
        return {"expires_at": existing.expires_at.isoformat()}
    token, row = consult.create_session(db, settings)
    response.set_cookie(COOKIE, token, max_age=settings.session_ttl_days * 86400, httponly=True,
                        secure=settings.cookie_secure, samesite="lax", path="/api")
    return {"expires_at": row.expires_at.isoformat()}


@router.get("/session")
def get_session(session: SessionDep):
    return {"expires_at": session.expires_at.isoformat()}


@router.post("/consultations", status_code=201, dependencies=Guard)
def create_consultation(body: ConsultationIn, session: SessionDep, db: DBDep, settings: SettingsDep):
    c = consult.create_consultation(db, settings, session, body.dog)
    return {"consultation_id": str(c.id), "dog": consult.dog_view(c), "life_stage": c.life_stage}


@router.get("/consultations/{consultation_id}/messages")
def get_messages(consultation_id: uuid.UUID, session: SessionDep, db: DBDep):
    return consult.consultation_messages(db, session, consultation_id)


@router.delete("/consultations/{consultation_id}", status_code=204, dependencies=Guard)
def delete_consultation(consultation_id: uuid.UUID, session: SessionDep, db: DBDep):
    consult.delete_consultation(db, session, consultation_id)
    return Response(status_code=204)


# ------------------------------------------------------------------ P02 / P07 questions and runs
@router.post("/consultations/{consultation_id}/questions", status_code=202, dependencies=Guard)
def post_question(consultation_id: uuid.UUID, body: QuestionIn, request: Request, session: SessionDep, db: DBDep,
                  settings: SettingsDep):
    run = consult.submit_question(db, settings, request.app.state.limiter, session, consultation_id, body.text,
                                  body.client_request_id)
    if run.status == "queued" and getattr(request.app.state, "worker", None):
        request.app.state.worker.notify()  # committed above; wake the idle worker
    return {"run_id": str(run.id), "status": run.status}


@router.get("/runs/{run_id}")
def get_run(run_id: uuid.UUID, session: SessionDep, db: DBDep):
    return consult.get_run(db, session, run_id)


# ------------------------------------------------------------------ P03 citations
@router.get("/citations/{citation_id}")
def get_citation(citation_id: uuid.UUID, session: SessionDep, db: DBDep, settings: SettingsDep):
    return consult.citation_detail(db, settings, session, citation_id)


# ------------------------------------------------------------------ P05 feedback
@router.put("/messages/{message_id}/feedback", dependencies=Guard)
def put_feedback(message_id: uuid.UUID, body: FeedbackIn, session: SessionDep, db: DBDep):
    fid = consult.put_feedback(db, session, message_id, body.helpful, body.comment)
    return {"feedback_id": str(fid)}


# ------------------------------------------------------------------ public settings for the UI
@router.get("/config")
def ui_config(settings: SettingsDep):
    return {"question_max_chars": settings.question_max_chars, "session_ttl_days": settings.session_ttl_days,
            "run_timeout_seconds": settings.run_timeout_seconds}


__all__ = ["router", "Field"]
