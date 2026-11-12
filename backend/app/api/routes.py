"""PoC API (docs/04 §2). All paths under /api/v1."""
from __future__ import annotations

import uuid
from typing import Annotated, Iterator

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator
from sqlalchemy.orm import Session as DB

from ..config import Settings
from ..db.models import Session as SessionRow
from ..domain.dog import DogProfileIn
from ..services import care, clinics, consult, profile, worker
from .errors import ApiError

COOKIE = "tvc_session"
PROFILE_COOKIE = "tvc_profile"
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


NAME_PATTERN = r"^[^\x00-\x1f\x7f<>\[\]{}`\x5c]+$"  # same rule as the dog name


class ClinicChoice(BaseModel):
    """A public-data clinic found by /clinics/match, or a guardian-typed name when there was no match.
    From Kakao only the place id and URL are accepted (and stored)."""
    model_config = ConfigDict(extra="forbid")
    clinic_id: uuid.UUID | None = None
    manual_name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=40,
                                                  pattern=NAME_PATTERN)] | None = None
    kakao_place_id: Annotated[str, StringConstraints(pattern=r"^\d{1,20}$")] | None = None
    kakao_place_url: Annotated[str, StringConstraints(max_length=200)] | None = None

    @model_validator(mode="after")
    def _one(self) -> "ClinicChoice":
        if (self.clinic_id is None) == (self.manual_name is None):
            raise ValueError("give either clinic_id or manual_name")
        return self


class ProfileIn(BaseModel):
    """`clinic` / `clinic_phone` left out = keep the saved ones; null = remove."""
    model_config = ConfigDict(extra="forbid")
    dog: DogProfileIn
    clinic: ClinicChoice | None = None
    clinic_phone: Annotated[str, StringConstraints(strip_whitespace=True, max_length=20)] | None = None


class MatchIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
    road_address: Annotated[str, StringConstraints(max_length=200)] | None = None
    address: Annotated[str, StringConstraints(max_length=200)] | None = None


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
def create_consultation(body: ConsultationIn, request: Request, session: SessionDep, db: DBDep, settings: SettingsDep):
    prof = profile.resolve(db, request.cookies.get(PROFILE_COOKIE))
    if prof is not None:
        profile.touch(db, settings, prof)  # committed with the consultation
    c = consult.create_consultation(db, settings, session, body.dog, prof)
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
def get_run(run_id: uuid.UUID, session: SessionDep, db: DBDep, settings: SettingsDep):
    if settings.run_mode == "request":  # no background sweeper: expire stuck runs while someone polls
        worker.sweep_timeouts(db, settings.run_timeout_seconds)
    return consult.get_run(db, session, run_id)


@router.post("/runs/{run_id}/execute", dependencies=Guard)
def execute_run(run_id: uuid.UUID, request: Request, session: SessionDep, db: DBDep, settings: SettingsDep):
    """Request mode (serverless): run a queued answer inside this request and return the finished run.
    Safe to call more than once or from several tabs — only the request that claims the run processes it;
    the others get the current state. In worker mode it only returns the current state."""
    view = consult.get_run(db, session, run_id)  # ownership check (404 for other sessions)
    if settings.run_mode != "request" or view["status"] != "queued":
        return view
    app = request.app
    if app.state.llm is None:
        raise ApiError(503, "internal_error")
    worker.sweep_timeouts(db, settings.run_timeout_seconds)
    if worker.claim_run(db, run_id):
        worker.process_run(app.state.Session, settings, app.state.llm, app.state.embedder, run_id)
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


# ------------------------------------------------------------------ P08 settings: dog profile + 주치동물병원
def _profile_cookie(response: Response, token: str, settings: Settings) -> None:
    response.set_cookie(PROFILE_COOKIE, token, max_age=settings.profile_ttl_days * 86400, httponly=True,
                        secure=settings.cookie_secure, samesite="lax", path="/api")


@router.get("/profile")
def get_profile(request: Request, response: Response, db: DBDep, settings: SettingsDep):
    token = request.cookies.get(PROFILE_COOKIE)
    row = profile.resolve(db, token)
    if row is None:
        raise ApiError(404, "profile_not_found")
    profile.touch(db, settings, row)
    db.commit()
    _profile_cookie(response, token, settings)  # keep the cookie as long as the row
    return profile.view(db, row)


@router.put("/profile", dependencies=Guard)
def put_profile(body: ProfileIn, request: Request, response: Response, db: DBDep, settings: SettingsDep):
    token = request.cookies.get(PROFILE_COOKIE)
    row = profile.resolve(db, token)
    new_token, row = profile.save(db, settings, row, body.dog, set_clinic="clinic" in body.model_fields_set,
                                  clinic_choice=body.clinic, clinic_phone=body.clinic_phone,
                                  set_phone="clinic_phone" in body.model_fields_set)
    _profile_cookie(response, new_token or token, settings)
    return profile.view(db, row)


@router.delete("/profile", status_code=204, dependencies=Guard)
def delete_profile(request: Request, db: DBDep):
    row = profile.resolve(db, request.cookies.get(PROFILE_COOKIE))
    if row is not None:
        profile.remove(db, row)
    out = Response(status_code=204)
    out.delete_cookie(PROFILE_COOKIE, path="/api")
    return out


def _client_key(request: Request) -> uuid.UUID:
    fwd = request.headers.get("x-forwarded-for", "")
    ip = fwd.split(",")[0].strip() or (request.client.host if request.client else "?")
    return uuid.uuid5(uuid.NAMESPACE_URL, "clinic-search:" + ip)


@router.get("/clinics/search")
def search_clinics(request: Request, settings: SettingsDep, q: str | None = None, x: float | None = None,
                   y: float | None = None, emergency: bool = False):
    """Kakao Local results passed through for display only: not stored, not logged (docs/03 §6)."""
    if not request.app.state.limiter.allow(_client_key(request), settings.clinic_searches_per_minute):
        raise ApiError(429, "rate_limited")
    if (x is None) != (y is None) or (x is not None and not (124 <= x <= 132 and 33 <= y <= 39.5)):
        raise ApiError(400, "invalid_location")
    if q is not None and len(q) > 60:
        raise ApiError(400, "query_too_long")
    return {"items": clinics.search(request.app.state.places, q, x, y, emergency)}


@router.post("/clinics/match", dependencies=Guard)
def match_clinic(body: MatchIn, db: DBDep):
    return {"clinic": clinics.clinic_view(clinics.match(db, body.name, body.road_address, body.address))}


# ------------------------------------------------------------------ P09 care
@router.get("/care/consultations")
def care_list(session: SessionDep, db: DBDep):
    return {"items": care.list_cards(db, session)}


@router.post("/consultations/{consultation_id}/summary", dependencies=Guard)
def care_summary(consultation_id: uuid.UUID, request: Request, session: SessionDep, db: DBDep, settings: SettingsDep):
    return care.summarize(db, settings, request.app.state.llm, session, consultation_id)


# ------------------------------------------------------------------ public settings for the UI
@router.get("/config")
def ui_config(settings: SettingsDep):
    return {"question_max_chars": settings.question_max_chars, "session_ttl_days": settings.session_ttl_days,
            "run_timeout_seconds": settings.run_timeout_seconds, "profile_ttl_days": settings.profile_ttl_days}


__all__ = ["router", "Field"]
