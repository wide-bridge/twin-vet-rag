"""FastAPI app: /api/v1 + (when built) the React SPA from frontend/dist on the same origin."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from sqlalchemy import text

from .api import errors
from .api.routes import router
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


def create_app(settings: Settings | None = None, *, llm=None, embedder=None, start_worker: bool = True) -> FastAPI:
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
    app.state.limiter = RateLimiter()
    errors.install(app)
    app.include_router(router)

    @app.get("/api/v1/live")
    def live() -> dict:
        """Process liveness without touching the DB: point platform health checks here so they do not
        keep a serverless DB awake. /api/v1/health checks the DB and is for manual checks."""
        return {"status": "ok"}

    @app.get("/api/v1/health")
    def health() -> JSONResponse:
        checks = {"db": "down", "openai_key": "present" if settings.openai_api_key else "missing"}
        try:
            with app.state.Session() as db:
                db.execute(text("SELECT 1"))
                has_vector = db.execute(text("SELECT 1 FROM pg_extension WHERE extname='vector'")).scalar()
                active = db.execute(text("SELECT count(*) FROM document WHERE status='active' AND active_version_id IS NOT NULL")).scalar()
            checks["db"] = "up"
            checks["pgvector"] = "available" if has_vector else "missing"
            checks["active_documents"] = int(active)
        except Exception:  # never leak connection strings or stack traces
            pass
        ok = checks["db"] == "up" and checks.get("pgvector") == "available"
        return JSONResponse({"status": "ok" if ok else "degraded", "checks": checks}, status_code=200 if ok else 503)

    if (DIST / "index.html").is_file():
        # low-priority static routes (API routes always win); on Vercel these files are promoted to the CDN
        app.frontend("/", directory=DIST, fallback="index.html")

    return app


app = create_app()
