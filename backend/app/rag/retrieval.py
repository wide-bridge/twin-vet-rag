"""Vector search over the active knowledge index (docs/04 §4 step 3)."""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Sequence

from sqlalchemy import text
from sqlalchemy.orm import Session

from ..domain.dog import LifeStage

_SEARCH_SQL = text(
    """
    SELECT c.id AS chunk_id, d.id AS document_id, d.document_type, d.title, d.department,
           d.disease_names, d.life_stages, c.content, c.embedding_version,
           1 - (c.embedding <=> CAST(:qvec AS vector)) AS score
    FROM chunk c
    JOIN document_version v ON v.id = c.document_version_id
    JOIN document d ON d.active_version_id = v.id
    WHERE d.status = 'active'
      AND v.status = 'active'
      AND c.embedding IS NOT NULL
      AND c.embedding_model = :model
      AND (CAST(:allow_mock AS boolean) OR coalesce(d.metadata->>'is_mock', 'false') <> 'true')
      AND (CAST(:life_stage AS text) IS NULL
           OR cardinality(d.life_stages) = 0
           OR d.life_stages && ARRAY[CAST(:life_stage AS text), 'common'])
    ORDER BY c.embedding <=> CAST(:qvec AS vector)
    LIMIT :top_k
    """
)


@dataclass(frozen=True)
class Hit:
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    document_type: str
    title: str
    department: str | None
    disease_names: list[str]
    life_stages: list[str]
    content: str
    index_version: str | None
    score: float


def _vec_literal(v: Sequence[float]) -> str:
    return "[" + ",".join(f"{x:.7f}" for x in v) + "]"


def search(db: Session, query_embedding: Sequence[float], *, model: str, life_stage: LifeStage | None, top_k: int = 6,
           ef_search: int = 80, allow_mock: bool | None = None) -> list[Hit]:
    """Filters (active doc + active version + same embedding model + life stage) are applied in
    the same query as the ANN scan. ef_search is raised so filtering does not starve top_k.
    Mock HITL knowledge (metadata.is_mock) is never returned in production, even if it was activated."""
    if allow_mock is None:
        from ..config import get_settings

        allow_mock = not get_settings().hitl_production
    db.execute(text(f"SET LOCAL hnsw.ef_search = {int(ef_search)}"))
    rows = db.execute(
        _SEARCH_SQL,
        {"qvec": _vec_literal(query_embedding), "model": model, "life_stage": life_stage.value if life_stage else None, "top_k": top_k,
         "allow_mock": bool(allow_mock)},
    ).mappings()
    return [
        Hit(r["chunk_id"], r["document_id"], r["document_type"], r["title"], r["department"], list(r["disease_names"]),
            list(r["life_stages"]), r["content"], r["embedding_version"], float(r["score"]))
        for r in rows
    ]
