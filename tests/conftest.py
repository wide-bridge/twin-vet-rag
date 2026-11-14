import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DB_TESTS = os.environ.get("TWINVET_DB_TESTS") == "1"


def _test_db_url() -> str:
    from backend.app.config import get_settings

    base = get_settings().database_url
    return base.rsplit("/", 1)[0] + "/twinvet_test"


@pytest.fixture(scope="session")
def test_db_url():
    """Fresh `twinvet_test` database migrated to head (the dev DB is never touched)."""
    if not DB_TESTS:
        pytest.skip("needs `docker compose up -d db`; set TWINVET_DB_TESTS=1")
    import psycopg
    from backend.app.config import get_settings

    from sqlalchemy.engine import make_url

    target = make_url(get_settings().database_url)
    # This fixture drops/recreates a DB. Never run it against Neon or another remote host.
    if target.host not in {"localhost", "127.0.0.1", "::1"}:
        pytest.fail("DB tests require a local PostgreSQL URL; remote databases are prohibited.")
    admin = get_settings().database_url.replace("postgresql+psycopg://", "postgresql://", 1)
    with psycopg.connect(admin, autocommit=True) as conn:
        conn.execute("DROP DATABASE IF EXISTS twinvet_test WITH (FORCE)")
        conn.execute("CREATE DATABASE twinvet_test")
    url = _test_db_url()
    env = {**os.environ, "ALEMBIC_DATABASE_URL": url}
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=ROOT, env=env, check=True, capture_output=True)
    return url


@pytest.fixture()
def db(test_db_url):
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker

    engine = create_engine(test_db_url, future=True)
    with engine.begin() as conn:
        conn.execute(text(
            "TRUNCATE session, consultation, message, answer_run, citation, feedback, dog_profile, clinic, "
            "document, document_version, chunk, ingestion_run, reviewer, reviewer_session, concept, concept_relation, "
            "review_item, review_label, label_concept, review_event, knowledge_candidate, "
            "vet_account, vet_session, vet_link, vet_question, vet_reply_revision, usage_daily CASCADE"
        ))
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()
