"""Daily retention job via Vercel Cron: closed without CRON_SECRET or with a wrong token; with the right token it
stores usage counts first and then deletes expired sessions (and their consultations)."""
from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import text

from backend.app.config import Settings
from backend.app.main import create_app
from backend.app.rag.embeddings import FakeEmbedder
from backend.app.rag.llm import FakeLLM

H = {"X-TwinVet-Client": "web"}
DOG = {"breed": "말티즈", "age_months": 30}


def _app(url, secret):
    return create_app(Settings(database_url=url, app_env="test", cookie_secure=False, cron_secret=secret),
                      llm=FakeLLM({}), embedder=FakeEmbedder(), start_worker=False)


def test_cron_endpoint_is_closed_without_the_secret(db, test_db_url):
    c = TestClient(_app(test_db_url, None))
    assert c.get("/api/v1/internal/cleanup").status_code == 404
    assert c.get("/api/v1/internal/cleanup", headers={"Authorization": "Bearer "}).status_code == 404
    c = TestClient(_app(test_db_url, "s3cret-value"))
    assert c.get("/api/v1/internal/cleanup").status_code == 404
    assert c.get("/api/v1/internal/cleanup", headers={"Authorization": "Bearer wrong"}).status_code == 404


def test_cron_stores_counts_then_deletes_expired(db, test_db_url):
    app = _app(test_db_url, "s3cret-value")
    g = TestClient(app, base_url="http://localhost:8000")
    g.post("/api/v1/sessions", headers=H)
    assert g.post("/api/v1/consultations", json={"dog": DOG}, headers=H).status_code == 201
    with app.state.Session() as s:  # the session expired two days ago
        s.execute(text("UPDATE session SET expires_at = now() - interval '2 days', created_at = now()"))
        s.commit()
    r = TestClient(app).get("/api/v1/internal/cleanup", headers={"Authorization": "Bearer s3cret-value"})
    assert r.status_code == 200 and r.json()["sessions_deleted"] == 1 and r.json()["usage_days_stored"] >= 1
    with app.state.Session() as s:
        assert s.execute(text("SELECT count(*) FROM consultation")).scalar() == 0
        today = s.execute(text("SELECT flow FROM usage_daily ORDER BY day DESC LIMIT 1")).scalar()
        assert today["consultations"] == 1  # counted before it was deleted
    assert "말티즈" not in r.text
