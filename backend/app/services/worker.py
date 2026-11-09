"""Answer-run worker (docs/04 §3). Run state lives in the DB, so a refresh or a restart never leaves a
question loading forever:
  claim    queued -> running with FOR UPDATE SKIP LOCKED (safe with several threads/processes)
  process  run the pipeline outside any transaction (model calls take seconds)
  save     re-lock the run; if it was deleted (consultation deleted) or timed out meanwhile, drop the result
  sweep    running longer than RUN_TIMEOUT_SECONDS -> failed(timeout)
Logs carry run ids and codes only — never question text, dog info or answers (docs/03 §3).
"""
from __future__ import annotations

import logging
import threading
import time
import uuid
from datetime import datetime, timezone

from sqlalchemy import select, text
from sqlalchemy.orm import Session as DB
from sqlalchemy.orm import sessionmaker

from ..config import Settings
from ..db.models import AnswerRun, Citation, Consultation, Message
from ..domain.dog import LifeStage
from ..rag.embeddings import Embedder
from ..rag.llm import LLM, LLMError
from ..rag.pipeline import PipelineResult, run_pipeline
from ..rag.prompts import PROMPT_VERSION, DogInfo, Turn

log = logging.getLogger("twinvet.worker")


def claim_next(db: DB) -> uuid.UUID | None:
    rid = db.execute(text(
        "UPDATE answer_run SET status = 'running', started_at = now() WHERE id = ("
        "  SELECT id FROM answer_run WHERE status = 'queued' ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1"
        ") RETURNING id")).scalar()
    db.commit()
    return rid


def _history(db: DB, run: AnswerRun, limit: int) -> list[Turn]:
    if limit <= 0:
        return []
    prev = db.execute(
        select(AnswerRun).where(AnswerRun.consultation_id == run.consultation_id, AnswerRun.id != run.id,
                                AnswerRun.status == "completed", AnswerRun.created_at < run.created_at)
        .order_by(AnswerRun.created_at.desc()).limit(limit)).scalars().all()
    turns = []
    for r in reversed(prev):
        q = db.get(Message, r.question_message_id)
        a = db.get(Message, r.answer_message_id) if r.answer_message_id else None
        if q:
            turns.append(Turn(q.content, a.content if a else None))
    return turns


def process_run(Session: sessionmaker, settings: Settings, llm: LLM, embedder: Embedder, run_id: uuid.UUID) -> str:
    """Returns the final status ('completed' / 'failed' / 'dropped')."""
    t0 = time.monotonic()
    with Session() as db:
        run = db.get(AnswerRun, run_id)
        if run is None:
            return "dropped"
        c = db.get(Consultation, run.consultation_id)
        question = db.get(Message, run.question_message_id).content
        dog = DogInfo(c.dog_breed, c.dog_age_months, float(c.dog_weight_kg) if c.dog_weight_kg is not None else None,
                      c.dog_neutered, c.dog_health_notes)
        history = _history(db, run, settings.history_turns)
        db.commit()  # end the read transaction; the search below runs in its own short one
        try:
            result = run_pipeline(db, settings=settings, llm=llm, embedder=embedder, dog=dog,
                                  life_stage=LifeStage(c.life_stage), history=history, question=question)
            db.rollback()
        except LLMError as e:
            log.warning("run %s failed: model_error (%s)", run_id, e)
            return _fail(db, run_id, "model_error")
        except Exception:
            log.exception("run %s failed: internal_error", run_id)
            return _fail(db, run_id, "internal_error")
    with Session() as db:
        return _save(db, run_id, result, settings, int((time.monotonic() - t0) * 1000))


def _fail(db: DB, run_id: uuid.UUID, code: str) -> str:
    db.rollback()
    n = db.execute(text("UPDATE answer_run SET status='failed', error_code=:c, finished_at=now() "
                        "WHERE id=:id AND status='running'"), {"c": code, "id": run_id}).rowcount
    db.commit()
    return "failed" if n else "dropped"


def _save(db: DB, run_id: uuid.UUID, r: PipelineResult, settings: Settings, latency_ms: int) -> str:
    run = db.execute(select(AnswerRun).where(AnswerRun.id == run_id).with_for_update()).scalar_one_or_none()
    if run is None or run.status != "running":  # consultation deleted, or timed out meanwhile
        db.rollback()
        return "dropped"
    db.execute(select(Consultation.id).where(Consultation.id == run.consultation_id).with_for_update())
    seq = db.execute(text("SELECT coalesce(max(sequence), 0) + 1 FROM message WHERE consultation_id = :c"),
                     {"c": run.consultation_id}).scalar()
    msg = Message(consultation_id=run.consultation_id, sequence=seq, role="assistant", content=r.text)
    db.add(msg)
    db.flush()
    run.status, run.response_type, run.answer_message_id = "completed", r.response_type, msg.id
    run.decision_stage, run.error_code = r.decision_stage, None
    run.model = r.models.get("answer")
    run.classifier_model = r.models.get("classifier")
    run.prompt_version, run.index_version = PROMPT_VERSION, r.index_version
    run.latency_ms = latency_ms
    # evaluation trace (DB only, never logged): rewritten query and every retrieved chunk with its score
    run.token_usage = {**r.token_usage, "search_query": r.search_query,
                       "retrieval": [{"chunk_id": str(h.chunk_id), "score": round(h.score, 4)} for h in r.retrieved]}
    run.finished_at = datetime.now(timezone.utc)
    for rank, h in enumerate(r.citations, 1):
        db.add(Citation(answer_run_id=run.id, chunk_id=h.chunk_id, title_snapshot=_citation_title(h), rank=rank,
                        score=h.score, excerpt=None))
    db.commit()
    return "completed"


def _citation_title(h) -> str:
    if h.document_type == "qa_case":
        return f"{h.department or ''} 상담 사례".strip()
    return h.title


def sweep_timeouts(db: DB, timeout_seconds: int) -> int:
    n = db.execute(text("UPDATE answer_run SET status='failed', error_code='timeout', finished_at=now() "
                        "WHERE status='running' AND started_at < now() - make_interval(secs => :s)"),
                   {"s": timeout_seconds}).rowcount
    db.commit()
    return n


def fail_interrupted(db: DB) -> int:
    """At startup: runs left 'running' by a previous process will never finish (single API process)."""
    n = db.execute(text("UPDATE answer_run SET status='failed', error_code='interrupted', finished_at=now() "
                        "WHERE status='running'")).rowcount
    db.commit()
    return n


class Worker:
    def __init__(self, Session: sessionmaker, settings: Settings, llm: LLM, embedder: Embedder):
        self.Session, self.settings, self.llm, self.embedder = Session, settings, llm, embedder
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []

    def start(self) -> None:
        with self.Session() as db:
            n = fail_interrupted(db)
            if n:
                log.warning("marked %d interrupted run(s) as failed", n)
        for i in range(self.settings.worker_threads):
            t = threading.Thread(target=self._loop, name=f"answer-worker-{i}", daemon=True)
            t.start()
            self._threads.append(t)

    def stop(self) -> None:
        self._stop.set()
        for t in self._threads:
            t.join(timeout=5)

    def _loop(self) -> None:
        last_sweep = 0.0
        while not self._stop.is_set():
            try:
                if time.monotonic() - last_sweep > 10:
                    with self.Session() as db:
                        sweep_timeouts(db, self.settings.run_timeout_seconds)
                    last_sweep = time.monotonic()
                with self.Session() as db:
                    rid = claim_next(db)
                if rid is None:
                    self._stop.wait(0.5)
                    continue
                status = process_run(self.Session, self.settings, self.llm, self.embedder, rid)
                log.info("run %s %s", rid, status)
            except Exception:
                log.exception("worker loop error")
                self._stop.wait(2)
