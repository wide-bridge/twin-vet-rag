"""Vercel entrypoint: Vercel looks for a FastAPI instance named `app` in ./index.py.
The same app runs locally with `uvicorn backend.app.main:app`. On Vercel (VERCEL is set) RUN_MODE defaults to
"request": no background worker; the browser calls POST /api/v1/runs/{id}/execute to run each answer."""
from backend.app.main import app  # noqa: F401
