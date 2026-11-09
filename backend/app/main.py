from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from sqlalchemy import create_engine, text

from .config import get_settings

app = FastAPI(title="TwinVet Care", version="0.1.0")


@app.get("/api/v1/health")
def health() -> JSONResponse:
    settings = get_settings()
    checks = {"db": "down", "openai_key": "present" if settings.openai_api_key else "missing"}
    try:
        engine = create_engine(settings.database_url, pool_pre_ping=True)
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
            has_vector = conn.execute(text("SELECT 1 FROM pg_available_extensions WHERE name='vector'")).scalar()
        checks["db"] = "up"
        checks["pgvector"] = "available" if has_vector else "missing"
        engine.dispose()
    except Exception:  # never leak connection strings or stack traces
        pass
    ok = checks["db"] == "up" and checks.get("pgvector") == "available"
    return JSONResponse({"status": "ok" if ok else "degraded", "checks": checks}, status_code=200 if ok else 503)
