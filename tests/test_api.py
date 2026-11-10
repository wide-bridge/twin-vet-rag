"""API + run worker on the throwaway test DB, with SYNTHETIC knowledge, FakeEmbedder and a scripted FakeLLM.
No network, no cost. Runs are driven synchronously (claim_next + process_run) instead of the thread worker."""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from backend.app.config import Settings
from backend.app.ingest import load
from backend.app.ingest.load import DocInput
from backend.app.main import create_app
from backend.app.rag.embeddings import FakeEmbedder
from backend.app.rag.llm import FakeLLM, LLMError
from backend.app.services import consult, worker

H = {"X-TwinVet-Client": "web"}
DOG = {"breed": "말티즈", "age_months": 123, "weight_kg": 3.2}
IN_SCOPE = {"scope": "in_scope", "search_query": "말티즈 구토", "question_age_text": "", "question_unclear": False,
            "clarification_question": ""}


def _knowledge(db):
    E = FakeEmbedder()
    run = load.start_run(db, "synthetic", "v1", E.model)
    docs = [
        DocInput("q1", "qa_case", "내과 상담 사례", "내과", [], ["senior"], "h-q1", "s/q1.json", {},
                 [{"ordinal": 0, "content": "보호자 질문: 노견 구토\n수의사 답변: 탈수를 확인하세요", "content_hash": "c", "tokens": 10, "locator": None}]),
        DocInput("r1", "reference", "소동물 내과학", "내과", [], [], "h-r1", "s/r1.json", {},
                 [{"ordinal": 0, "content": "구토의 원인은 다양하다.", "content_hash": "c0", "tokens": 10, "locator": {"char_start": 0, "char_end": 5}}]),
    ]
    load.stage(db, run, docs)
    load.embed_pending(db, run, E)
    assert load.validate(db, run, E.model, sample=2)["ok"]
    load.activate(db, run)


@pytest.fixture()
def env(db, test_db_url):
    _knowledge(db)
    llm = FakeLLM({"classify": IN_SCOPE,
                   "answer": {"response_type": "grounded", "answer": "입력하신 10세 말티즈 기준으로 탈수를 확인하세요.", "used_sources": [2, 99, 2]}})
    settings = Settings(database_url=test_db_url, cookie_secure=False, worker_threads=0, retrieval_min_score=-1.0,
                        questions_per_minute=100)
    app = create_app(settings, llm=llm, embedder=FakeEmbedder(), start_worker=False)
    client = TestClient(app, base_url="http://localhost:8000")

    def drain():
        out = []
        while True:
            with app.state.Session() as s:
                rid = worker.claim_next(s)
            if rid is None:
                return out
            out.append(worker.process_run(app.state.Session, settings, llm, app.state.embedder, rid))

    class Env:
        pass

    e = Env()
    e.app, e.client, e.llm, e.settings, e.drain = app, client, llm, settings, drain
    return e


def start(client) -> str:
    assert client.post("/api/v1/sessions", headers=H).status_code in (200, 201)
    r = client.post("/api/v1/consultations", json={"dog": DOG}, headers=H)
    assert r.status_code == 201, r.json()
    return r.json()["consultation_id"]


def ask(client, cid, q="어제부터 두 번 토했어요", crid=None):
    return client.post(f"/api/v1/consultations/{cid}/questions", json={"text": q, "client_request_id": crid or uuid.uuid4().hex}, headers=H)


# ------------------------------------------------------------------ sessions & consultations
def test_session_cookie_and_reuse(env):
    r = env.client.post("/api/v1/sessions", headers=H)
    assert r.status_code == 201 and "tvc_session" in r.cookies
    assert "httponly" in r.headers["set-cookie"].lower()
    assert env.client.post("/api/v1/sessions", headers=H).status_code == 200  # same browser keeps its session


def test_consultation_validation_and_life_stage(env):
    env.client.post("/api/v1/sessions", headers=H)
    bad = env.client.post("/api/v1/consultations", json={"dog": {"breed": " ", "age_months": 400}}, headers=H)
    assert bad.status_code == 422 and set(bad.json()["error"]["fields"]) == {"dog.breed", "dog.age_months"}
    assert env.client.post("/api/v1/consultations", json={"dog": {**DOG, "life_stage": "puppy"}}, headers=H).status_code == 422
    r = env.client.post("/api/v1/consultations", json={"dog": DOG}, headers=H).json()
    assert r["life_stage"] == "senior" and r["dog"]["weight_kg"] == 3.2


def test_consultation_with_birth_date(env):
    env.client.post("/api/v1/sessions", headers=H)
    r = env.client.post("/api/v1/consultations", headers=H,
                        json={"dog": {"breed": "푸들", "birth_year": 2020, "birth_month": 5, "birth_day": 20}})
    assert r.status_code == 201, r.text
    dog = r.json()["dog"]
    assert (dog["birth_year"], dog["birth_month"], dog["birth_day"], dog["age_basis"]) == (2020, 5, 20, "day")
    assert dog["age_months"] >= 77
    r = env.client.post("/api/v1/consultations", headers=H, json={"dog": {"breed": "푸들", "birth_year": 2020}})
    assert r.json()["dog"]["birth_month"] is None and r.json()["dog"]["age_basis"] == "year"
    future = env.client.post("/api/v1/consultations", headers=H, json={"dog": {"breed": "푸들", "birth_year": 2099}})
    assert future.status_code == 422 and future.json()["error"]["code"] == "invalid_birth_date"
    both = env.client.post("/api/v1/consultations", headers=H, json={"dog": {"breed": "푸들", "birth_year": 2020, "age_months": 3}})
    assert both.status_code == 422


def test_auth_and_csrf_guards(env):
    assert env.client.post("/api/v1/consultations", json={"dog": DOG}, headers=H).status_code == 401
    assert env.client.post("/api/v1/sessions").status_code == 403  # no client header
    assert env.client.post("/api/v1/sessions", headers={**H, "Origin": "https://evil.example"}).status_code == 403
    assert env.client.post("/api/v1/sessions", headers={**H, "Origin": "http://localhost:5173"}).status_code == 201


# ------------------------------------------------------------------ answer flow
def test_grounded_answer_with_server_checked_citations(env):
    cid = start(env.client)
    r = ask(env.client, cid)
    assert r.status_code == 202 and r.json()["status"] == "queued"
    run_id = r.json()["run_id"]
    assert env.client.get(f"/api/v1/runs/{run_id}").json()["answer"] is None
    assert env.drain() == ["completed"]
    run = env.client.get(f"/api/v1/runs/{run_id}").json()
    assert run["status"] == "completed" and run["response_type"] == "grounded"
    assert "10세 말티즈" in run["answer"]["text"]
    assert len(run["citations"]) == 1  # 99 is not a search result; the duplicate 2 counted once
    # the dog block is passed as guardian input, separate from the question
    assert "<보호자 입력 정보>" in env.llm.calls[-1]["user"] and "말티즈" in env.llm.calls[-1]["user"]

    msgs = env.client.get(f"/api/v1/consultations/{cid}/messages").json()
    assert [m["role"] for m in msgs["messages"]] == ["user", "assistant"]
    assert msgs["messages"][1]["citations"] == run["citations"]
    detail = env.client.get(f"/api/v1/citations/{run['citations'][0]['id']}").json()
    assert detail["availability"] == "available" and detail["excerpt"] is None and detail["excerpt_allowed"] is False
    assert detail["source_name"] and "s/" not in str(detail)  # no local paths


@pytest.mark.parametrize("classify, answer, min_score, expected, stage", [
    ({**IN_SCOPE, "scope": "out_of_scope", "search_query": ""}, None, -1.0, "out_of_scope", "scope"),
    ({**IN_SCOPE, "question_unclear": True, "clarification_question": "토한 내용물에 피가 보였나요?"}, None, -1.0, "clarification", "clarify"),
    (IN_SCOPE, None, 1.0, "insufficient_evidence", "retrieval"),
    (IN_SCOPE, {"response_type": "grounded", "answer": "근거 없는 답", "used_sources": [42]}, -1.0, "insufficient_evidence", "answer"),
    (IN_SCOPE, {"response_type": "insufficient_evidence", "answer": "", "used_sources": []}, -1.0, "insufficient_evidence", "answer"),
])
def test_response_types(env, classify, answer, min_score, expected, stage):
    env.llm.responses["classify"] = classify
    if answer:
        env.llm.responses["answer"] = answer
    env.settings.retrieval_min_score = min_score
    cid = start(env.client)
    run_id = ask(env.client, cid).json()["run_id"]
    env.drain()
    run = env.client.get(f"/api/v1/runs/{run_id}").json()
    assert run["response_type"] == expected and run["citations"] == []
    with env.app.state.Session() as s:
        assert s.execute(text("SELECT decision_stage FROM answer_run WHERE id=:i"), {"i": run_id}).scalar() == stage
    if expected == "clarification":
        assert run["answer"]["text"] == "토한 내용물에 피가 보였나요?"
    if stage in ("scope", "clarify", "retrieval"):
        assert [c["name"] for c in env.llm.calls] == ["classify"]  # answer model not called


def test_follow_up_gets_history(env):
    cid = start(env.client)
    ask(env.client, cid, "어제부터 두 번 토했어요")
    env.drain()
    ask(env.client, cid, "그럼 병원은 언제 가야 하나요?")
    env.drain()
    assert "[이전 질문 1] 어제부터 두 번 토했어요" in env.llm.calls[-1]["user"]


# ------------------------------------------------------------------ duplicates, busy, failure, retry
def test_idempotency_conflict_and_busy(env):
    cid = start(env.client)
    crid = uuid.uuid4().hex
    a = ask(env.client, cid, "질문 A", crid).json()
    assert ask(env.client, cid, "질문 A", crid).json()["run_id"] == a["run_id"]  # re-send: same run, no 409
    assert ask(env.client, cid, "질문 B", crid).status_code == 409  # same id, different text
    busy = ask(env.client, cid, "질문 C")
    assert busy.status_code == 409 and busy.json()["error"]["code"] == "busy"
    env.drain()
    msgs = env.client.get(f"/api/v1/consultations/{cid}/messages").json()["messages"]
    assert [m["content"] for m in msgs if m["role"] == "user"] == ["질문 A"]  # stored once


def test_failure_then_explicit_retry(env):
    env.llm.responses["classify"] = LLMError("RateLimitError")
    cid = start(env.client)
    crid = uuid.uuid4().hex
    run_id = ask(env.client, cid, "질문", crid).json()["run_id"]
    assert env.drain() == ["failed"]
    run = env.client.get(f"/api/v1/runs/{run_id}").json()
    assert run["status"] == "failed" and run["error"]["retryable"] and "RateLimit" not in str(run)
    env.llm.responses["classify"] = IN_SCOPE
    again = ask(env.client, cid, "질문", crid).json()
    assert again == {"run_id": run_id, "status": "queued"}
    assert env.drain() == ["completed"]
    with env.app.state.Session() as s:
        assert s.execute(text("SELECT attempt FROM answer_run WHERE id=:i"), {"i": run_id}).scalar() == 2
        assert s.execute(text("SELECT count(*) FROM message WHERE role='user'")).scalar() == 1


def test_empty_and_too_long_questions(env):
    cid = start(env.client)
    assert ask(env.client, cid, "   ").json()["error"]["code"] == "empty_question"
    assert ask(env.client, cid, "가" * 1001).json()["error"]["code"] == "question_too_long"


def test_rate_limit(env):
    env.settings.questions_per_minute = 1
    cid = start(env.client)
    ask(env.client, cid)
    env.drain()
    assert ask(env.client, cid).status_code == 429


def test_timeout_sweep_and_restart_recovery(env):
    cid = start(env.client)
    run_id = ask(env.client, cid).json()["run_id"]
    with env.app.state.Session() as s:
        worker.claim_next(s)
        s.execute(text("UPDATE answer_run SET started_at = now() - interval '10 minutes'"))
        s.commit()
        assert worker.sweep_timeouts(s, 120) == 1
    run = env.client.get(f"/api/v1/runs/{run_id}").json()
    assert run["status"] == "failed" and run["error"]["code"] == "timeout"


# ------------------------------------------------------------------ ownership, feedback, delete, expiry
def test_other_session_cannot_see_or_write(env, test_db_url):
    cid = start(env.client)
    run_id = ask(env.client, cid).json()["run_id"]
    env.drain()
    run = env.client.get(f"/api/v1/runs/{run_id}").json()
    other = TestClient(env.app, base_url="http://localhost:8000")
    other.post("/api/v1/sessions", headers=H)
    assert other.get(f"/api/v1/consultations/{cid}/messages").status_code == 404
    assert other.get(f"/api/v1/runs/{run_id}").status_code == 404
    assert other.get(f"/api/v1/citations/{run['citations'][0]['id']}").status_code == 404
    assert other.put(f"/api/v1/messages/{run['answer']['message_id']}/feedback", json={"helpful": True}, headers=H).status_code == 404
    assert other.delete(f"/api/v1/consultations/{cid}", headers=H).status_code == 404


def test_feedback_upsert(env):
    cid = start(env.client)
    run_id = ask(env.client, cid).json()["run_id"]
    env.drain()
    mid = env.client.get(f"/api/v1/runs/{run_id}").json()["answer"]["message_id"]
    a = env.client.put(f"/api/v1/messages/{mid}/feedback", json={"helpful": True}, headers=H).json()
    b = env.client.put(f"/api/v1/messages/{mid}/feedback", json={"helpful": False, "comment": "더 자세히"}, headers=H).json()
    assert a["feedback_id"] == b["feedback_id"]
    msg = env.client.get(f"/api/v1/consultations/{cid}/messages").json()["messages"][1]
    assert msg["feedback"] == {"helpful": False, "comment": "더 자세히"}
    qid = env.client.get(f"/api/v1/consultations/{cid}/messages").json()["messages"][0]["id"]
    assert env.client.put(f"/api/v1/messages/{qid}/feedback", json={"helpful": True}, headers=H).status_code == 404


def test_delete_drops_in_flight_result(env):
    cid = start(env.client)
    ask(env.client, cid)
    with env.app.state.Session() as s:
        rid = worker.claim_next(s)
    assert env.client.delete(f"/api/v1/consultations/{cid}", headers=H).status_code == 204
    assert worker.process_run(env.app.state.Session, env.settings, env.llm, env.app.state.embedder, rid) == "dropped"
    assert env.client.get(f"/api/v1/consultations/{cid}/messages").status_code == 404
    with env.app.state.Session() as s:
        assert s.execute(text("SELECT count(*) FROM message")).scalar() == 0


def test_expired_session_and_purge(env):
    cid = start(env.client)
    with env.app.state.Session() as s:
        s.execute(text("UPDATE session SET expires_at = now() - interval '2 days'"))
        s.commit()
    r = env.client.get(f"/api/v1/consultations/{cid}/messages")
    assert r.status_code == 401 and r.json()["error"]["code"] == "session_expired"
    with env.app.state.Session() as s:
        assert consult.purge_expired_sessions(s) == 1
        assert s.execute(text("SELECT count(*) FROM consultation")).scalar() == 0


# ------------------------------------------------------------------ request mode (serverless, e.g. Vercel)
def test_execute_runs_answer_in_request_mode(env):
    env.settings.run_mode = "request"
    cid = start(env.client)
    run_id = ask(env.client, cid).json()["run_id"]
    assert env.client.get(f"/api/v1/runs/{run_id}").json()["status"] == "queued"
    r = env.client.post(f"/api/v1/runs/{run_id}/execute", headers=H)
    assert r.status_code == 200 and r.json()["status"] == "completed" and r.json()["response_type"] == "grounded"
    calls = len(env.llm.calls)
    again = env.client.post(f"/api/v1/runs/{run_id}/execute", headers=H).json()  # second tab / retry of the call
    assert again["status"] == "completed" and len(env.llm.calls) == calls        # not processed twice
    assert env.client.post(f"/api/v1/runs/{run_id}/execute").status_code == 403  # CSRF guard applies


def test_execute_is_a_no_op_in_worker_mode_and_owned(env):
    cid = start(env.client)
    run_id = ask(env.client, cid).json()["run_id"]
    assert env.client.post(f"/api/v1/runs/{run_id}/execute", headers=H).json()["status"] == "queued"
    assert env.llm.calls == []
    other = TestClient(env.app, base_url="http://localhost:8000")
    other.post("/api/v1/sessions", headers=H)
    assert other.post(f"/api/v1/runs/{run_id}/execute", headers=H).status_code == 404


def test_request_mode_failed_run_can_be_retried(env):
    env.settings.run_mode = "request"
    env.llm.responses["classify"] = LLMError("boom")
    cid = start(env.client)
    crid = uuid.uuid4().hex
    run_id = ask(env.client, cid, crid=crid).json()["run_id"]
    assert env.client.post(f"/api/v1/runs/{run_id}/execute", headers=H).json()["status"] == "failed"
    env.llm.responses["classify"] = IN_SCOPE
    assert ask(env.client, cid, crid=crid).json()["run_id"] == run_id               # same run back to queued
    assert env.client.post(f"/api/v1/runs/{run_id}/execute", headers=H).json()["status"] == "completed"
