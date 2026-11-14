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
from .api import m03_routes
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
VET_API = "/api/v1/vet"
# answer on every host (liveness, DB check, and the M03 switch both apps read)
ALWAYS_OPEN = ("/api/v1/live", "/api/v1/health", "/api/v1/m03/availability", "/api/v1/internal/cleanup")


def _request_host(request: Request) -> str:
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or ""
    return host.split(",")[0].strip().split(":")[0].lower()


def _under(path: str, prefix: str) -> bool:
    return path == prefix or path.startswith(prefix + "/")


def _install_host_split(app: FastAPI, settings: Settings) -> None:
    """Separate hosts (docs/05 S10, docs/04 §7-1): REVIEW_HOST answers only the review API, VET_HOST only the vet
    API; other hosts answer neither of those. Responses on those hosts are marked noindex. Static pages are served by
    the CDN on Vercel, so redirects and page headers live in vercel.json."""
    hosts = {h: p for h, p in ((settings.review_host.strip().lower(), REVIEW_API),
                               (settings.vet_host.strip().lower(), VET_API)) if h}
    if not hosts:
        return

    @app.middleware("http")
    async def host_split(request: Request, call_next):
        path = request.url.path
        own = hosts.get(_request_host(request))  # this host's only API, or None for the guardian host
        if path.startswith("/api/") and path not in ALWAYS_OPEN:
            if own is not None:
                allowed = _under(path, own)
            else:
                allowed = not any(_under(path, p) for p in hosts.values())
            if not allowed:
                out = JSONResponse({"error": {"code": "not_found", "message": "찾을 수 없어요."}}, status_code=404)
            else:
                out = await call_next(request)
        else:
            out = await call_next(request)
        if own is not None:
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
    _install_host_split(app, settings)
    app.include_router(router)
    app.include_router(review_router)
    app.include_router(m03_routes.public)
    app.include_router(m03_routes.guardian)
    app.include_router(m03_routes.vet)

    @app.get("/api/v1/internal/cleanup")
    def scheduled_cleanup(request: Request) -> JSONResponse:
        """Daily retention job for Vercel Cron. Answers 404 unless CRON_SECRET is set and the bearer token matches."""
        import hmac

        from .services.maintenance import run_cleanup

        secret = settings.cron_secret.get_secret_value() if settings.cron_secret else ""
        got = request.headers.get("authorization", "")
        if not secret or not hmac.compare_digest(got.encode(), f"Bearer {secret}".encode()):
            return JSONResponse({"error": {"code": "not_found", "message": "찾을 수 없어요."}}, status_code=404)
        with app.state.Session() as db:
            out = run_cleanup(db, settings)
        log.info("scheduled cleanup: %s", out)  # counts only
        return JSONResponse({"ok": True, **out})

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
