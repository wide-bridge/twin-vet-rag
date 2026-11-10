"""Answer pipeline (docs/04 §4). Decision order:
  (1)(2) one classifier call: out_of_scope / clarification / search query      -> decision_stage scope|clarify
  (3)    vector search; best score below RETRIEVAL_MIN_SCORE -> insufficient   -> decision_stage retrieval
  (4)    answer model with numbered sources; source numbers checked against
         this search; grounded without a valid source -> insufficient          -> decision_stage answer
A model failure raises LLMError (run becomes failed, retryable); no type is guessed.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from ..config import Settings
from ..domain.dog import LifeStage, derive_life_stage
from .embeddings import Embedder
from .llm import LLM, LLMError
from .prompts import (
    ANSWER_SCHEMA, ANSWER_SYSTEM, CLASSIFY_SCHEMA, CLASSIFY_SYSTEM, TEXT_INSUFFICIENT, TEXT_OUT_OF_SCOPE, TEXT_UNCLEAR,
    DogInfo, Turn, age_conflict_text, age_text, answer_user, classify_user, source_block,
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


# A long question almost always says what it is about; the "unclear" flag is only trusted on short ones.
UNCLEAR_MAX_CHARS = 60
# Ages are approximate; only a life-stage change by more than this many months counts as a conflict.
AGE_CONFLICT_MIN_MONTHS = 24


_NUM_KO = {"한": 1, "두": 2, "세": 3, "네": 4, "다섯": 5, "여섯": 6, "일곱": 7, "여덟": 8, "아홉": 9, "열": 10,
           "열한": 11, "열두": 12, "열세": 13, "열네": 14, "열다섯": 15}
_YEARS = re.compile(r"(\d{1,2}|열다섯|열한|열두|열세|열네|다섯|여섯|일곱|여덟|아홉|한|두|세|네|열)\s*(?:살|세|년)(\s*반)?")
_MONTHS = re.compile(r"(\d{1,3})\s*개월")
_KO = "열다섯|열한|열두|열세|열네|다섯|여섯|일곱|여덟|아홉|한|두|세|네|열"
# fallback when the model misses it: an age phrase used as the dog's current age ("10살 된", "3개월인", "5세입니다")
_AGE_PHRASE = rf"((?:\d{{1,2}}|{_KO})\s*(?:살|세|년)(?:\s*반)?(?:\s*\d{{1,2}}\s*개월)?|\d{{1,3}}\s*개월)"
_ANY_AGE = re.compile(_AGE_PHRASE)
_STATED_AGE = re.compile(_AGE_PHRASE + r"\s*(?:된|인|이에요|예요|입니다|이며|이고|짜리|의 나이)")


def parse_age_months(phrase: str) -> int | None:
    """'5개월' -> 5, '10살' -> 120, '두 살 반' -> 30, '2년 3개월' -> 27; None when no age is found."""
    y, m = _YEARS.search(phrase), _MONTHS.search(phrase)
    if not y and not m:
        return None
    months = 0
    if y:
        n = y.group(1)
        months += (int(n) if n.isdigit() else _NUM_KO[n]) * 12 + (6 if y.group(2) else 0)
    if m:
        months += int(m.group(1))
    return months


def stated_age_months(question: str) -> int | None:
    """The dog's current age written in a question ("10살 된", "8년 3개월의 나이"), by pattern only."""
    m = _STATED_AGE.search(question)
    return parse_age_months(m.group(1)) if m else None


def first_age_months(text: str) -> int | None:
    """First age-like phrase anywhere in the text (loose; for building eval dog profiles, not for decisions)."""
    m = _ANY_AGE.search(text)
    return parse_age_months(m.group(1)) if m else None


def question_age(data: dict, question: str) -> int | None:
    """The dog's current age stated in the question: the model's verbatim phrase if it really occurs in
    the question, else the pattern fallback. None when absent or out of range."""
    phrase = (data.get("question_age_text") or "").strip()
    if not phrase or phrase not in question:
        m = _STATED_AGE.search(question)
        phrase = m.group(1) if m else ""
    q_age = parse_age_months(phrase) if phrase else None
    return q_age if q_age is not None and q_age <= 371 else None


def resolve_age(data: dict, dog: DogInfo, life_stage: LifeStage, settings: Settings,
                question: str) -> tuple[str | None, LifeStage]:
    """(clarification text or None, life stage to search with).
    A question age that falls in another life stage and differs by more than 2 years:
      - input from a birth date (day/month): ask which is right (docs/04 §4 (2))
      - input year-only or estimated: follow the question and search its life stage"""
    q_age = question_age(data, question)
    if q_age is None:
        return None, life_stage
    q_stage = derive_life_stage(q_age, settings.life_stage_puppy_max_months, settings.life_stage_senior_min_months)
    if q_stage == life_stage or abs(q_age - dog.age_months) <= AGE_CONFLICT_MIN_MONTHS:
        return None, life_stage
    if dog.age_reliable:
        return age_conflict_text(dog.age_text(), age_text(q_age)), life_stage
    return None, q_stage


def clarification(data: dict, dog: DogInfo, life_stage: LifeStage, settings: Settings, *,
                  has_history: bool, question: str) -> str | None:
    """Server-side clarification decision (docs/04 §4 (2)); returns the text to ask, or None to answer.
    (a) age conflict with a birth-date input (resolve_age)
    (b) the question says nothing answerable — trusted only for a short question without prior turns"""
    conflict, _ = resolve_age(data, dog, life_stage, settings, question)
    return conflict or unclear_text(data, has_history=has_history, question=question)


def unclear_text(data: dict, *, has_history: bool, question: str) -> str | None:
    if data.get("question_unclear") and not has_history and len(question.strip()) <= UNCLEAR_MAX_CHARS:
        return (data.get("clarification_question") or "").strip() or TEXT_UNCLEAR
    return None


def _add_usage(usage: dict, key: str, r) -> None:
    usage[key] = {"model": r.model, "input": r.input_tokens, "output": r.output_tokens}


def run_pipeline(db: Session, *, settings: Settings, llm: LLM, embedder: Embedder, dog: DogInfo,
                 life_stage: LifeStage, history: list[Turn], question: str) -> PipelineResult:
    usage: dict = {}
    models = {"classifier": settings.llm_classifier_model}

    # (1)(2) scope + clarification + search query
    c = llm.complete_json(model=settings.llm_classifier_model, system=CLASSIFY_SYSTEM,
                          user=classify_user(dog, history, question), schema=CLASSIFY_SCHEMA, name="classify",
                          reasoning="low")  # minimal confused other animals with the dog (2026-11-11 probe)
    _add_usage(usage, "classify", c)
    if c.data.get("scope") not in ("in_scope", "out_of_scope"):
        raise LLMError("invalid_classification")
    if c.data["scope"] == "out_of_scope":
        # "도와주세요" says nothing at all; asking what is wrong beats refusing it as out of scope
        vague = unclear_text(c.data, has_history=bool(history), question=question)
        if vague:
            return PipelineResult("clarification", vague, "clarify", token_usage=usage, models=models)
        return PipelineResult("out_of_scope", TEXT_OUT_OF_SCOPE, "scope", token_usage=usage, models=models)
    clarify = clarification(c.data, dog, life_stage, settings, has_history=bool(history), question=question)
    if clarify:
        return PipelineResult("clarification", clarify, "clarify", token_usage=usage, models=models)
    _, life_stage = resolve_age(c.data, dog, life_stage, settings, question)  # approximate input yields

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
