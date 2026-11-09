"""Load prepared JSONL (prepare.py output) into PostgreSQL + pgvector.

Stages (each resumable, docs/02 §2 운영자 흐름):
  stage     -> document upsert, new document_version(status=staging) + chunks, only when content changed
  embed     -> fill chunk.embedding for this run's staging chunks (batched, committed per batch)
  validate  -> every staged chunk embedded with the right model; self-retrieval sample check
  activate  -> one transaction: previous active version -> inactive, staged -> active
A failure before `activate` leaves the currently active index untouched.
"""
from __future__ import annotations

import json
import random
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from sqlalchemy import bindparam, func, select, text, update
from sqlalchemy.orm import Session

from ..db.models import Chunk, Document, DocumentVersion, IngestionRun
from ..rag.embeddings import Embedder

SOURCE_NAME = "AI Hub 반려견 성장 및 질병 관련 말뭉치"
USAGE_NOTE = "AI Hub 개방데이터(59). 이용 조건 확인 전: 원문 발췌 비공개"


def index_version(dataset_code: str, prepared_version: str, model: str) -> str:
    return f"{dataset_code}/{prepared_version}+{model}"


@dataclass
class DocInput:
    source_record_id: str
    document_type: str
    title: str
    department: str | None
    disease_names: list[str]
    life_stages: list[str]
    content_hash: str
    storage_ref: str
    meta: dict
    chunks: list[dict] = field(default_factory=list)  # {ordinal, content, content_hash, tokens, locator}


def _read_jsonl(path: Path) -> Iterable[dict]:
    with path.open(encoding="utf-8") as f:
        for line in f:
            if line.strip():
                yield json.loads(line)


def read_prepared(prepared_dir: Path, limit: int | None = None) -> tuple[str, list[DocInput]]:
    docs: list[DocInput] = []
    dataset_code = None
    for r in _read_jsonl(prepared_dir / "qa_cases.jsonl"):
        dataset_code = r["dataset_code"]
        docs.append(DocInput(
            r["source_record_id"], "qa_case", r["title"], r["department"], r["disease_names"], r["life_stages"],
            r["content_hash"], r["source_relative_path"], r.get("metadata", {}),
            [{"ordinal": 0, "content": r["content"], "content_hash": r["content_hash"], "tokens": r["tokens"], "locator": None}],
        ))
    chunks_by_doc: dict[str, list[dict]] = defaultdict(list)
    for c in _read_jsonl(prepared_dir / "reference_chunks.jsonl"):
        chunks_by_doc[c["source_record_id"]].append(c)
    for r in _read_jsonl(prepared_dir / "reference_docs.jsonl"):
        dataset_code = r["dataset_code"]
        meta = {**r.get("metadata", {}), "author": r.get("author"), "publisher": r.get("publisher")}
        chunks = sorted(chunks_by_doc.get(r["source_record_id"], []), key=lambda c: c["ordinal"])
        docs.append(DocInput(
            r["source_record_id"], "reference", r["title"], r["department"], r["disease_names"], r["life_stages"],
            r["content_hash"], r["source_relative_path"], meta,
            [{"ordinal": c["ordinal"], "content": c["content"], "content_hash": c["content_hash"], "tokens": c["tokens"], "locator": c["locator"]} for c in chunks],
        ))
    if dataset_code is None:
        raise ValueError("prepared directory is empty")
    if limit is not None:
        random.Random(0).shuffle(docs)
        docs = docs[:limit]
    return dataset_code, docs


# ---------------------------------------------------------------- run bookkeeping
def start_run(db: Session, dataset_code: str, prepared_version: str, embedding_model: str) -> IngestionRun:
    run = IngestionRun(dataset_code=dataset_code, prepared_version=prepared_version, embedding_model=embedding_model, stage="stage")
    db.add(run)
    db.commit()
    return run


def _finish(db: Session, run: IngestionRun, status: str, error_code: str | None = None) -> None:
    run.status, run.error_code, run.finished_at = status, error_code, datetime.now(timezone.utc)
    db.commit()


# ---------------------------------------------------------------- stage
def stage(db: Session, run: IngestionRun, docs: list[DocInput], batch: int = 1000) -> dict:
    """Create staging versions only for new/changed content. Re-running reuses an existing
    staging/failed version with the same hash (so already-embedded chunks are not paid for twice)."""
    existing = {
        row.source_record_id: row
        for row in db.execute(
            select(Document.id, Document.source_record_id, Document.status).where(Document.dataset_code == run.dataset_code)
        )
    }
    latest: dict = {}
    for row in db.execute(
        select(DocumentVersion.document_id, DocumentVersion.id, DocumentVersion.version, DocumentVersion.content_hash,
               DocumentVersion.status, IngestionRun.embedding_model)
        .join(IngestionRun, IngestionRun.id == DocumentVersion.ingestion_run_id, isouter=True)
        .order_by(DocumentVersion.document_id, DocumentVersion.version.desc())
    ):
        latest.setdefault(row.document_id, row)  # first row per document = highest version
    counts = {"new": 0, "changed": 0, "unchanged": 0, "reused_staging": 0, "skipped_deleted": 0, "chunks": 0}
    pending = 0
    for d in docs:
        ex = existing.get(d.source_record_id)
        if ex is None:
            doc = Document(
                dataset_code=run.dataset_code, source_record_id=d.source_record_id, document_type=d.document_type,
                title=d.title, department=d.department, disease_names=d.disease_names, life_stages=d.life_stages,
                source_name=SOURCE_NAME, usage_note=USAGE_NOTE, meta=d.meta,
            )
            db.add(doc)
            db.flush()
            doc_id, next_version = doc.id, 1
            counts["new"] += 1
        else:
            doc_id = ex.id
            if ex.status == "deleted":
                counts["skipped_deleted"] += 1
                continue
            lv = latest.get(doc_id)
            # same content AND same embedding model = nothing to do; a model switch re-indexes
            if lv is not None and lv.content_hash == d.content_hash and lv.embedding_model == run.embedding_model:
                if lv.status == "active":
                    counts["unchanged"] += 1
                    continue
                if lv.status in ("staging", "failed"):
                    db.execute(update(DocumentVersion).where(DocumentVersion.id == lv.id).values(status="staging", ingestion_run_id=run.id))
                    counts["reused_staging"] += 1
                    continue
            next_version = (lv.version + 1) if lv else 1
            db.execute(
                update(Document).where(Document.id == doc_id).values(
                    title=d.title, department=d.department, disease_names=d.disease_names, life_stages=d.life_stages, meta=d.meta,
                )
            )
            counts["changed"] += 1
        ver = DocumentVersion(
            document_id=doc_id, version=next_version, title=d.title, content_hash=d.content_hash,
            storage_ref=d.storage_ref, prepared_version=run.prepared_version, ingestion_run_id=run.id, status="staging",
        )
        db.add(ver)
        db.flush()
        db.execute(
            Chunk.__table__.insert(),
            [{"id": uuid.uuid4(), "document_version_id": ver.id, **c} for c in d.chunks],
        )
        counts["chunks"] += len(d.chunks)
        pending += 1
        if pending >= batch:
            db.commit()
            pending = 0
    run.counts = {**(run.counts or {}), "stage": counts}
    run.stage = "embed"
    db.commit()
    return counts


# ---------------------------------------------------------------- embed
HEADER_TOKENS_ESTIMATE = 25  # per chunk, for the cost estimate


def embedding_text(document_type: str, title: str, department: str | None, disease_names: list[str] | None, content: str) -> str:
    """Text sent to the embedding model: a one-line source header + the chunk. The header gives a
    bare paragraph its context (which book, which department). chunk.content stays header-free
    for display; queries are embedded as typed."""
    if document_type == "reference":
        head = f"자료명: {title}" + (f" · 진료과: {department}" if department else "")
    else:
        head = f"진료과: {department or '미상'}" + (f" · 질병: {', '.join(disease_names)}" if disease_names else "")
    return f"{head}\n{content}"


def pending_chunk_stats(db: Session, run: IngestionRun) -> dict:
    row = db.execute(
        select(func.count(Chunk.id), func.coalesce(func.sum(Chunk.tokens), 0))
        .join(DocumentVersion, DocumentVersion.id == Chunk.document_version_id)
        .where(DocumentVersion.ingestion_run_id == run.id, DocumentVersion.status == "staging", Chunk.embedding.is_(None))
    ).one()
    return {"chunks": int(row[0]), "tokens": int(row[1]) + int(row[0]) * HEADER_TOKENS_ESTIMATE}


def embed_pending(db: Session, run: IngestionRun, embedder: Embedder, batch: int = 256, max_tokens_per_batch: int = 250_000, progress=None) -> dict:
    ivers = index_version(run.dataset_code, run.prepared_version, embedder.model)
    done = 0
    while True:
        rows = db.execute(
            select(Chunk.id, Chunk.content, Chunk.tokens, Document.document_type, DocumentVersion.title,
                   Document.department, Document.disease_names)
            .join(DocumentVersion, DocumentVersion.id == Chunk.document_version_id)
            .join(Document, Document.id == DocumentVersion.document_id)
            .where(DocumentVersion.ingestion_run_id == run.id, DocumentVersion.status == "staging", Chunk.embedding.is_(None))
            .order_by(Chunk.id)
            .limit(batch)
        ).all()
        if not rows:
            break
        picked, tokens = [], 0
        for r in rows:  # keep each API request under the token budget
            if picked and tokens + r.tokens + HEADER_TOKENS_ESTIMATE > max_tokens_per_batch:
                break
            picked.append(r)
            tokens += r.tokens + HEADER_TOKENS_ESTIMATE
        vectors = embedder.embed([embedding_text(r.document_type, r.title, r.department, r.disease_names, r.content) for r in picked])
        db.execute(
            update(Chunk.__table__).where(Chunk.__table__.c.id == bindparam("cid")).values(
                embedding=bindparam("emb"), embedding_model=embedder.model, embedding_version=ivers,
            ),
            [{"cid": r.id, "emb": v} for r, v in zip(picked, vectors)],
        )
        db.commit()  # resumable: committed batches are never re-embedded
        done += len(picked)
        if progress:
            progress(done)
    run.counts = {**(run.counts or {}), "embed": {"chunks": done, "model": embedder.model, "index_version": ivers}}
    run.stage = "validate"
    db.commit()
    return {"embedded": done, "index_version": ivers}


# ---------------------------------------------------------------- validate
def validate(db: Session, run: IngestionRun, embedding_model: str, sample: int = 20) -> dict:
    missing = db.execute(
        select(func.count(Chunk.id))
        .join(DocumentVersion, DocumentVersion.id == Chunk.document_version_id)
        .where(DocumentVersion.ingestion_run_id == run.id, DocumentVersion.status == "staging",
               (Chunk.embedding.is_(None)) | (Chunk.embedding_model != embedding_model))
    ).scalar_one()
    empty_versions = db.execute(text(
        "SELECT count(*) FROM document_version v WHERE v.ingestion_run_id = :rid AND v.status = 'staging' "
        "AND NOT EXISTS (SELECT 1 FROM chunk c WHERE c.document_version_id = v.id)"
    ), {"rid": run.id}).scalar_one()
    # self-retrieval: a staged chunk's own vector must find itself first among staged chunks
    ids = db.execute(text(
        "SELECT c.id FROM chunk c JOIN document_version v ON v.id = c.document_version_id "
        "WHERE v.ingestion_run_id = :rid AND v.status = 'staging' AND c.embedding IS NOT NULL ORDER BY random() LIMIT :n"
    ), {"rid": run.id, "n": sample}).scalars().all()
    hits = 0
    for cid in ids:
        top = db.execute(text(
            "SELECT c.id FROM chunk c JOIN document_version v ON v.id = c.document_version_id "
            "WHERE v.ingestion_run_id = :rid AND v.status = 'staging' "
            "ORDER BY c.embedding <=> (SELECT embedding FROM chunk WHERE id = :cid) LIMIT 1"
        ), {"rid": run.id, "cid": cid}).scalar_one()
        hits += int(top == cid)
    result = {"missing_embeddings": int(missing), "empty_versions": int(empty_versions), "self_retrieval": f"{hits}/{len(ids)}"}
    ok = missing == 0 and empty_versions == 0 and hits == len(ids)
    run.counts = {**(run.counts or {}), "validate": {**result, "ok": ok}}
    run.stage = "activate" if ok else "validate"
    db.commit()
    return {**result, "ok": ok}


# ---------------------------------------------------------------- activate / deactivate / delete
def activate(db: Session, run: IngestionRun) -> dict:
    """Atomic switch. Only versions staged by this run are promoted."""
    staged = db.execute(
        select(DocumentVersion.id, DocumentVersion.document_id).where(
            DocumentVersion.ingestion_run_id == run.id, DocumentVersion.status == "staging")
    ).all()
    if not staged:
        _finish(db, run, "completed")
        return {"activated": 0}
    doc_ids = [r.document_id for r in staged]
    ver_ids = [r.id for r in staged]
    db.execute(update(DocumentVersion).where(DocumentVersion.document_id.in_(doc_ids), DocumentVersion.status == "active").values(status="inactive"))
    db.execute(update(DocumentVersion).where(DocumentVersion.id.in_(ver_ids)).values(status="active"))
    # document.status is left alone: an operator's inactive/deleted decision survives reloads
    doc_t = Document.__table__
    db.execute(
        update(doc_t).where(doc_t.c.id == bindparam("did")).values(active_version_id=bindparam("vid")),
        [{"did": d, "vid": v} for v, d in staged],
    )
    run.counts = {**(run.counts or {}), "activate": {"versions": len(staged)}}
    run.stage = None
    run.status, run.finished_at = "completed", datetime.now(timezone.utc)
    db.commit()
    return {"activated": len(staged)}


def fail_run(db: Session, run: IngestionRun, error_code: str) -> None:
    db.rollback()
    db.execute(update(DocumentVersion).where(DocumentVersion.ingestion_run_id == run.id, DocumentVersion.status == "staging").values(status="failed"))
    _finish(db, run, "failed", error_code)


def set_document_status(db: Session, dataset_code: str, source_record_ids: list[str], status: str) -> int:
    """inactive: excluded from search immediately, data kept. deleted: chunks removed too
    (citations keep their title snapshot and report the source as unavailable)."""
    if status not in ("active", "inactive", "deleted"):
        raise ValueError(status)
    docs = db.execute(select(Document.id).where(Document.dataset_code == dataset_code, Document.source_record_id.in_(source_record_ids))).scalars().all()
    if not docs:
        return 0
    db.execute(update(Document).where(Document.id.in_(docs)).values(status=status))
    if status == "deleted":
        db.execute(text(
            "DELETE FROM chunk WHERE document_version_id IN (SELECT id FROM document_version WHERE document_id = ANY(:ids))"
        ), {"ids": list(docs)})
        db.execute(update(DocumentVersion).where(DocumentVersion.document_id.in_(docs)).values(status="inactive"))
    db.commit()
    return len(docs)
