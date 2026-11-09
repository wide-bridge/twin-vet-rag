"""pgvector end-to-end check: extension enable -> store -> index -> similarity search.

Runs against the local compose DB (`docker compose up -d db`); opt in with TWINVET_DB_TESTS=1.
Everything happens inside one transaction that is rolled back, so the DB is left unchanged.
"""
import os

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("TWINVET_DB_TESTS") != "1", reason="needs `docker compose up -d db`; set TWINVET_DB_TESTS=1"
)


def _psycopg_url() -> str:
    from backend.app.config import get_settings

    return get_settings().database_url.replace("postgresql+psycopg://", "postgresql://", 1)


def test_vector_extension_store_and_search():
    import numpy as np
    import psycopg
    from pgvector.psycopg import register_vector

    with psycopg.connect(_psycopg_url()) as conn:
        try:
            conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
            enabled = conn.execute("SELECT extversion FROM pg_extension WHERE extname = 'vector'").fetchone()
            assert enabled is not None, "vector extension not enabled"

            register_vector(conn)
            conn.execute("CREATE TEMP TABLE vec_probe (id int PRIMARY KEY, label text, embedding vector(3)) ON COMMIT DROP")
            conn.execute("CREATE INDEX ON vec_probe USING hnsw (embedding vector_cosine_ops)")
            rows = [
                (1, "x-axis", np.array([1.0, 0.0, 0.0])),
                (2, "y-axis", np.array([0.0, 1.0, 0.0])),
                (3, "near-x", np.array([0.9, 0.1, 0.0])),
            ]
            with conn.cursor() as cur:
                cur.executemany("INSERT INTO vec_probe VALUES (%s, %s, %s)", rows)

            stored = conn.execute("SELECT embedding FROM vec_probe WHERE id = 2").fetchone()[0]
            stored = stored.to_numpy() if hasattr(stored, "to_numpy") else np.asarray(stored)
            assert np.allclose(stored, [0.0, 1.0, 0.0])

            query = np.array([1.0, 0.05, 0.0])
            result = conn.execute(
                "SELECT label, 1 - (embedding <=> %s) AS similarity FROM vec_probe ORDER BY embedding <=> %s LIMIT 2",
                (query, query),
            ).fetchall()
            assert [r[0] for r in result] == ["x-axis", "near-x"]
            assert result[0][1] > 0.99
        finally:
            conn.rollback()
