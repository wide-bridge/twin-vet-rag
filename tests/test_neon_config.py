"""Offline regressions for cloud DB configuration (no network or API calls)."""
import os
import runpy
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_alembic_accepts_url_encoded_password():
    env = {**os.environ, "ENV_FILE": "", "ALEMBIC_DATABASE_URL":
           "postgresql+psycopg://test:p%40ss%25word@invalid.example/db?sslmode=require"}
    result = subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head", "--sql"],
                            cwd=ROOT, env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert "CREATE TABLE" in result.stdout


def test_db_fixture_rejects_remote_before_connect(monkeypatch):
    import backend.app.config as config
    import psycopg

    monkeypatch.setenv("TWINVET_DB_TESTS", "1")
    monkeypatch.setattr(config, "get_settings", lambda: SimpleNamespace(
        database_url="postgresql+psycopg://test:fake@invalid.example/db?sslmode=require"))

    def forbidden_connect(*args, **kwargs):
        raise AssertionError("Remote connection must never be attempted")

    monkeypatch.setattr(psycopg, "connect", forbidden_connect)
    fixture = runpy.run_path(str(ROOT / "tests" / "conftest.py"))["test_db_url"]
    with pytest.raises(pytest.fail.Exception, match="remote databases are prohibited"):
        fixture.__wrapped__()
