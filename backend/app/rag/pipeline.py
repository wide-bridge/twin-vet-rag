"""Answer pipeline (docs/04 §4). Decision order:
  (1)(2) one classifier call: out_of_scope / clarification / search query      -> decision_stage scope|clarify
  (3)    vector search; best score below RETRIEVAL_MIN_SCORE -> insufficient   -> decision_stage retrieval
  (4)    answer model with numbered sources; source numbers checked against
         this search; grounded without a valid source -> insufficient          -> decision_stage answer
A model failure raises LLMError (run becomes failed, retryable); no type is guessed.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from ..config import Settings
from ..domain.dog import LifeStage
from .embeddings import Embedder
from .llm import LLM, LLMError
from .prompts import (
    ANSWER_SCHEMA, ANSWER_SYSTEM, CLASSIFY_SCHEMA, CLASSIFY_SYSTEM, TEXT_INSUFFICIENT, TEXT_OUT_OF_SCOPE,
    DogInfo, Turn, answer_user, classify_user, source_block,
)
from .retrieval import Hit, search


@dataclass
class PipelineResult:
    response_type: str  # grounded | clarification | insufficient_evidence | out_of_scope
    text: str
    decision_stage: str  # scope | clarify | retrieval | answer
    citations: list[Hit] = field(default_factory=list)  # in the order the answer model listed them
    retrieved: list[Hit] = field(default_factory=list)
    search_query: str | None = None
    index_version: str | None = None
    token_usage: dict = field(default_factory=dict)
    models: dict = field(default_factory=dict)


def _add_usage(usage: dict, key: str, r) -> None:
    usage[key] = {"model": r.model, "input": r.input_tokens, "output": r.output_tokens}


def run_pipeline(db: Session, *, settings: Settings, llm: LLM, embedder: Embedder, dog: DogInfo,
                 life_stage: LifeStage, history: list[Turn], question: str) -> PipelineResult:
    usage: dict = {}
    models = {"classifier": settings.llm_classifier_model}

    # (1)(2) scope + clarification + search query
    c = llm.complete_json(model=settings.llm_classifier_model, system=CLASSIFY_SYSTEM,
                          user=classify_user(dog, history, question), schema=CLASSIFY_SCHEMA, name="classify")
    _add_usage(usage, "classify", c)
    if c.data.get("scope") not in ("in_scope", "out_of_scope"):
        raise LLMError("invalid_classification")
    if c.data["scope"] == "out_of_scope":
        return PipelineResult("out_of_scope", TEXT_OUT_OF_SCOPE, "scope", token_usage=usage, models=models)
    if c.data.get("needs_clarification") and (c.data.get("clarification_question") or "").strip():
        return PipelineResult("clarification", c.data["clarification_question"].strip(), "clarify",
                              token_usage=usage, models=models)

    # (3) retrieval: rewritten query (guardian-style, with dog info) — fall back to the raw question
    query = (c.data.get("search_query") or "").strip() or question
    vec = embedder.embed([query])[0]
    usage["embedding"] = {"model": embedder.model}
    hits = search(db, vec, model=embedder.model, life_stage=life_stage, top_k=settings.retrieval_top_k)
    db.rollback()  # read-only; do not hold a transaction open during the answer model call
    index_version = hits[0].index_version if hits else None
    if not hits or hits[0].score < settings.retrieval_min_score:
        return PipelineResult("insufficient_evidence", TEXT_INSUFFICIENT, "retrieval", retrieved=hits,
                              search_query=query, index_version=index_version, token_usage=usage, models=models)

    # (4) grounded answer; source numbers are checked against this search only
    models["answer"] = settings.llm_model
    sources = [source_block(i, h.document_type, h.title, h.department, h.content) for i, h in enumerate(hits, 1)]
    a = llm.complete_json(model=settings.llm_model, system=ANSWER_SYSTEM,
                          user=answer_user(dog, history, question, sources), schema=ANSWER_SCHEMA,
                          name="answer", reasoning="low")
    _add_usage(usage, "answer", a)
    used: list[Hit] = []
    for n in a.data.get("used_sources") or []:
        if isinstance(n, int) and 1 <= n <= len(hits) and hits[n - 1] not in used:
            used.append(hits[n - 1])
    text = (a.data.get("answer") or "").strip()
    if a.data.get("response_type") != "grounded" or not used or not text:
        return PipelineResult("insufficient_evidence", TEXT_INSUFFICIENT, "answer", retrieved=hits,
                              search_query=query, index_version=index_version, token_usage=usage, models=models)
    return PipelineResult("grounded", text, "answer", citations=used, retrieved=hits, search_query=query,
                          index_version=index_version, token_usage=usage, models=models)
