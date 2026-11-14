"""FastAPI app: /api/v1 + (when built) the React SPA from frontend/dist on the same origin."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text

from .api import errors
from .api.routes import router
from .api.review_routes import router as review_router
from .config import PROJECT_ROOT, Settings, get_settings
from .db.engine import session_factory
from .services.consult import RateLimiter

log = logging.getLogger("twinvet")
DIST = PROJECT_ROOT / "frontend" / "dist"


def _default_ai(settings: Settings):
    if not settings.openai_api_key:
        return None, None
    from .rag.embeddings import OpenAIEmbedder
    from .rag.llm import OpenAILLM

    key = settings.openai_api_key.get_secret_value()
    return OpenAILLM(key, timeout=settings.llm_timeout_seconds), OpenAIEmbedder(key, settings.embedding_model)


REVIEW_API = "/api/v1/review"
ALWAYS_OPEN = ("/api/v1/live", "/api/v1/health")


def _request_host(request: Request) -> str:
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or ""
    return host.split(",")[0].strip().split(":")[0].lower()


def _install_review_host(app: FastAPI, settings: Settings) -> None:
    """Separate host for the review screens (docs/05 S10): the review host answers only the review API, other hosts
    do not answer it; every response on the review host is marked noindex. Static pages are served by the CDN on
    Vercel, so the /review redirect and page headers live in vercel.json."""
    review_host = settings.review_host.strip().lower()
    if not review_host:
        return

    @app.middleware("http")
    async def review_host_split(request: Request, call_next):
        path = request.url.path
        on_review = _request_host(request) == review_host
        is_review_api = path == REVIEW_API or path.startswith(REVIEW_API + "/")
        if path.startswith("/api/") and path not in ALWAYS_OPEN and is_review_api != on_review:
            out = JSONResponse({"error": {"code": "not_found", "message": "찾을 수 없어요."}}, status_code=404)
        else:
            out = await call_next(request)
        if on_review:
            out.headers["X-Robots-Tag"] = "noindex, nofollow"
        return out


def create_app(settings: Settings | None = None, *, llm=None, embedder=None, places=None,
               start_worker: bool = True) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        worker = None
        if start_worker and settings.run_mode == "worker" and settings.worker_threads > 0:
            if app.state.llm is None:
                log.error("OPENAI_API_KEY missing: questions will stay queued")
            else:
                from .services.worker import Worker

                worker = Worker(app.state.Session, settings, app.state.llm, app.state.embedder)
                worker.start()
        app.state.worker = worker
        yield
        if worker:
            worker.stop()

    app = FastAPI(title="TwinVet Care", version="0.2.0", lifespan=lifespan, docs_url="/api/docs", redoc_url=None,
                  openapi_url="/api/openapi.json")
    app.state.settings = settings
    app.state.Session = session_factory(settings.database_url)
    if llm is None and embedder is None:
        llm, embedder = _default_ai(settings)
    app.state.llm, app.state.embedder = llm, embedder
    if places is None and settings.kakao_rest_api_key:
        from .services.clinics import KakaoLocal

        places = KakaoLocal(settings.kakao_rest_api_key.get_secret_value())
    app.state.places = places  # None: clinic search answers 503 (the rest of the app works)
    app.state.limiter = RateLimiter()
    errors.install(app)
    _install_review_host(app, settings)
    app.include_router(router)
    app.include_router(review_router)

    @app.get("/api/v1/live")
    def live() -> dict:
        """Process liveness without touching the DB: point platform health checks here so they do not
        keep a serverless DB awake. /api/v1/health checks the DB and is for manual checks."""
        return {"status": "ok"}

    @app.get("/api/v1/health")
    def health() -> JSONResponse:
        checks = {"db": "down", "openai_key": "present" if settings.openai_api_key else "missing",
                  "kakao_key": "present" if settings.kakao_rest_api_key else "missing"}
        try:
            with app.state.Session() as db:
                db.execute(text("SELECT 1"))
                has_vector = db.execute(text("SELECT 1 FROM pg_extension WHERE extname='vector'")).scalar()
                active = db.execute(text("SELECT count(*) FROM document WHERE status='active' AND active_version_id IS NOT NULL")).scalar()
                clinics = db.execute(text("SELECT count(*) FROM clinic WHERE source='public'")).scalar()
            checks["db"] = "up"
            checks["pgvector"] = "available" if has_vector else "missing"
            checks["active_documents"] = int(active)
            checks["public_clinics"] = int(clinics)
        except Exception:  # never leak connection strings or stack traces
            pass
        ok = checks["db"] == "up" and checks.get("pgvector") == "available"
        return JSONResponse({"status": "ok" if ok else "degraded", "checks": checks}, status_code=200 if ok else 503)

    if (DIST / "index.html").is_file():
        # low-priority static routes (API routes always win); on Vercel these files are promoted to the CDN
        app.frontend("/", directory=DIST, fallback="index.html")

    return app


app = create_app()
