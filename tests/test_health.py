import os

import pytest
from fastapi.testclient import TestClient

from backend.app.main import app


@pytest.mark.skipif(os.environ.get("TWINVET_DB_TESTS") != "1", reason="needs `docker compose up -d db`; set TWINVET_DB_TESTS=1")
def test_health_with_db():
    r = TestClient(app).get("/api/v1/health")
    assert r.status_code == 200, r.json()
    body = r.json()
    assert body["checks"]["db"] == "up" and body["checks"]["pgvector"] == "available"
    assert "postgresql" not in r.text  # no connection string leak
