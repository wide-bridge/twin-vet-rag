"""Comparison runs and re-check keep the first run's inputs (docs/02 §4-1 비교 입력 고정), prompt variants are
selectable without changing the app default, eval_final and indexed cases are refused. Fake LLM, no network."""
import json

import pytest
from sqlalchemy import select

from backend.app.config import Settings
from backend.app.db.models_review import ReviewItem
from backend.app.ingest import load
from backend.app.ingest.load import DocInput
from backend.app.rag import prompts
from backend.app.rag.embeddings import FakeEmbedder
from backend.app.rag.llm import FakeLLM
from backend.app.services import compare_run, review_dataset as ds
from backend.app.services import review as svc

E = FakeEmbedder()
CHECK = {"retrieval": "relevant", "claims": [], "citation_mismatch": [], "missing_conditions": [],
         "forbidden_content": [], "reference_agreement": "agree", "needs_medical_judgment": False, "summary": "ok"}


def _index(db):
    run = load.start_run(db, "synthetic", "v1", E.model)
    load.stage(db, run, [DocInput("q1", "qa_case", "내과 상담 사례", "내과", [], [], "h-q1", "s/q1.json", {},
                                  [{"ordinal": 0, "content": "보호자 질문: 구토\n수의사 답변: 탈수 확인", "content_hash": "c",
                                    "tokens": 10, "locator": None}])])
    load.embed_pending(db, run, E)
    load.activate(db, run)


def _llm():
    return FakeLLM({"classify": {"scope": "in_scope", "search_query": "구토", "question_age_text": "",
                                 "question_unclear": False, "clarification_question": ""},
                    "answer": {"response_type": "grounded", "answer": "탈수를 확인하세요.", "used_sources": [1]},
                    "auto_check": CHECK})


ROWS = {f"aihub59-dev-{i}": {"eval_id": f"aihub59-dev-{i}", "source_record_id": f"src-{i}", "department": "내과",
                             "life_stages": ["adult"], "question": f"강아지가 토해요 {i}", "reference_answer": "탈수 확인"}
        for i in range(3)}


@pytest.fixture()
def env(db, test_db_url):
    _index(db)
    return Settings(database_url=test_db_url, retrieval_min_score=-1.0, app_env="test")


def test_second_run_reuses_fixed_inputs_and_records_everything(db, env, monkeypatch):
    spec = [{"id": "aihub59-dev-0", "role": "dev"}, {"id": "aihub59-dev-1", "role": "confirm"}]
    cases = compare_run.build_inputs(db, env, spec, ROWS)
    # pretend the first run had a specific dog: a later run must use exactly this, not re-derive from the question
    cases[0]["inputs"]["dog"]["age_months"], cases[0]["inputs"]["life_stage"] = 120, "senior"
    run1 = compare_run.run_cases(db, cases, settings=env, llm=_llm(), embedder=E, prompt="current", data_label="v1")
    c = run1["cases"][0]
    assert c["role"] == "dev" and c["result"]["response_type"] == "grounded" and c["result"]["limited_at"] is None
    h = c["result"]["retrieved"][0]
    assert h["chunk_id"] and h["version_id"] and h["content"].startswith("보호자 질문")  # id, version and body kept
    assert run1["config"]["prompt_variant"] == "current" and run1["config"]["check_version"] == ds.CHECK_VERSION

    seen = []
    real = ds.run_pipeline

    def spy(db_, **kw):
        seen.append((kw["dog"].age_months, kw["life_stage"].value, kw["answer_system"]))
        return real(db_, **kw)

    monkeypatch.setattr(ds, "run_pipeline", spy)
    monkeypatch.setitem(prompts.ANSWER_SYSTEM_VARIANTS, "strict", "테스트용 수정 프롬프트")
    llm2 = _llm()
    run2 = compare_run.run_cases(db, compare_run.inputs_from(run1), settings=env, llm=llm2, embedder=E, prompt="strict")
    assert seen[0] == (120, "senior", "테스트용 수정 프롬프트")
    assert [c["inputs"] for c in run2["cases"]] == [c["inputs"] for c in run1["cases"]]
    assert any(call["name"] == "answer" and call["system"] == "테스트용 수정 프롬프트" for call in llm2.calls)
    assert prompts.ANSWER_SYSTEM_VARIANTS["current"] == prompts.ANSWER_SYSTEM  # app default unchanged


def test_rescore_does_not_generate_answers(db, env):
    run1 = compare_run.run_cases(db, compare_run.build_inputs(db, env, [{"id": "aihub59-dev-2", "role": "dev"}], ROWS),
                                 settings=env, llm=_llm(), embedder=E)
    llm = _llm()
    out = compare_run.rescore(json.loads(json.dumps(run1, default=str)), settings=env, llm=llm)
    assert [c["name"] for c in llm.calls] == ["auto_check"]
    assert out["kind"] == "rescore" and out["rescored_from"] == run1["run_id"]
    assert out["cases"][0]["result"]["answer"] == run1["cases"][0]["result"]["answer"]


def test_eval_final_and_indexed_cases_are_refused(db, env):
    with pytest.raises(compare_run.CaseError, match="not in the allowed subset"):
        compare_run.build_inputs(db, env, [{"id": "aihub59-final-x", "role": "dev"}], ROWS)
    with pytest.raises(compare_run.CaseError, match="role"):
        compare_run.build_inputs(db, env, [{"id": "aihub59-dev-0", "role": "other"}], ROWS)
    cases = compare_run.build_inputs(db, env, [{"id": "aihub59-dev-0", "role": "dev"}], ROWS)
    cases[0]["inputs"]["source_record_id"] = "q1"  # this one is in the index
    with pytest.raises(compare_run.CaseError, match="already in the search index"):
        compare_run.run_cases(db, cases, settings=env, llm=_llm(), embedder=E)
    with pytest.raises(compare_run.CaseError, match="unknown prompt"):
        compare_run.run_cases(db, compare_run.build_inputs(db, env, [{"id": "aihub59-dev-0", "role": "dev"}], ROWS),
                              settings=env, llm=_llm(), embedder=E, prompt="nope")


def test_recheck_uses_the_stored_dog_not_the_question(db, env, monkeypatch):
    """Bundle answered for a 10-year-old (senior); the question says 3 years -> re-check must still use 120 / senior."""
    item, _ = svc.create_item(db, origin="dataset", source_ref="aihub59-dev-9", question="3살 강아지가 토해요",
                              ai_answer="x", dog={"breed": "모름", "age_months": 120, "life_stage": "senior"},
                              response_type="grounded", versions={"department": "내과"})
    db.commit()
    seen = []
    real = ds.run_pipeline

    def spy(db_, **kw):
        seen.append((kw["dog"].age_months, kw["life_stage"].value))
        return real(db_, **kw)

    monkeypatch.setattr(ds, "run_pipeline", spy)
    out = ds.recheck_item(db, item, settings=env, llm=_llm(), embedder=E)
    assert seen == [(120, "senior")] and out["inputs"]["life_stage"] == "senior"
    # a bundle created by dataset-items stores the life stage it was answered with
    rep = ds.build_dataset_items(db, [{"eval_id": "aihub59-dev-8", "subset": "dev", "department": "내과",
                                       "life_stages": ["adult"], "question": "강아지가 토해요"}],
                                 settings=env, llm=_llm(), embedder=E, n=1)
    assert rep.created == 1
    it = db.execute(select(ReviewItem).where(ReviewItem.source_ref == "aihub59-dev-8")).scalar_one()
    assert it.dog["life_stage"] == "adult" and it.dog["age_months"] == 48


def test_rerun_refuses_changed_search_settings(db, env):
    """Codex P2: --inputs-from must not silently run with other search settings or models."""
    run1 = compare_run.run_cases(db, compare_run.build_inputs(db, env, [{"id": "aihub59-dev-0", "role": "dev"}], ROWS),
                                 settings=env, llm=_llm(), embedder=E)
    assert compare_run.config_mismatch(run1["config"], env, E) == {}
    other = env.model_copy(update={"retrieval_top_k": env.retrieval_top_k + 2, "llm_model": "other-model"})
    diff = compare_run.config_mismatch(run1["config"], other, E)
    assert set(diff) == {"top_k", "answer_model"}
    assert diff["top_k"] == {"before": env.retrieval_top_k, "now": env.retrieval_top_k + 2} and "answer_model" in diff


def test_rescore_never_overwrites(db, env, tmp_path):
    """Codex P2: rescoring the same run twice keeps both records."""
    run1 = compare_run.run_cases(db, compare_run.build_inputs(db, env, [{"id": "aihub59-dev-1", "role": "dev"}], ROWS),
                                 settings=env, llm=_llm(), embedder=E)
    run1 = json.loads(json.dumps(run1, default=str))
    r1 = compare_run.rescore(run1, settings=env, llm=_llm())
    r2 = compare_run.rescore(run1, settings=env, llm=_llm())
    assert r1["run_id"] != r2["run_id"] and r1["rescored_from"] == r2["rescored_from"] == run1["run_id"]
    p1, p2 = compare_run.write(r1, tmp_path), compare_run.write(r2, tmp_path)
    assert p1 != p2 and p1.exists() and p2.exists()
    with pytest.raises(FileExistsError):
        compare_run.write(r1, tmp_path)
