"""Review screens on their own host (docs/05 S10, 2026-11-14): with REVIEW_HOST set, that host answers only the
review API and is marked noindex; other hosts do not answer the review API. No DB needed (the split answers first)."""
from fastapi.testclient import TestClient

from backend.app.config import Settings
from backend.app.main import create_app
from backend.app.rag.embeddings import FakeEmbedder
from backend.app.rag.llm import FakeLLM

URL = "postgresql+psycopg://u:p@localhost:1/none"


def _app(review_host: str):
    s = Settings(database_url=URL, app_env="test", review_host=review_host)
    return create_app(s, llm=FakeLLM({}), embedder=FakeEmbedder(), start_worker=False)


def test_review_host_serves_only_the_review_api():
    app = _app("vet.example.test")
    vet = TestClient(app, base_url="https://vet.example.test")
    guardian = TestClient(app, base_url="https://twinvet.example.test")
    h = {"x-twinvet-client": "web"}

    r = vet.post("/api/v1/sessions", headers=h)  # guardian API on the review host
    assert r.status_code == 404 and r.headers["x-robots-tag"] == "noindex, nofollow"
    r = guardian.get("/api/v1/review/me")  # review API on the guardian host
    assert r.status_code == 404 and "x-robots-tag" not in r.headers
    r = vet.get("/api/v1/review/me")  # review API on the review host reaches the route (not logged in)
    assert r.status_code == 401 and r.json()["error"]["code"] == "review_login_required"
    for c in (vet, guardian):  # liveness stays open on both
        assert c.get("/api/v1/live").status_code == 200
    assert vet.get("/api/v1/live").headers["x-robots-tag"] == "noindex, nofollow"
    # behind a proxy the original host comes in x-forwarded-host
    r = TestClient(app, base_url="https://internal").get("/api/v1/review/me",
                                                          headers={"x-forwarded-host": "twinvet.example.test"})
    assert r.status_code == 404


def test_without_review_host_nothing_is_split():
    app = _app("")
    r = TestClient(app, base_url="https://twinvet.example.test").get("/api/v1/live")
    assert r.status_code == 200 and "x-robots-tag" not in r.headers
    assert not any(getattr(m, "kwargs", {}).get("dispatch") for m in app.user_middleware)
