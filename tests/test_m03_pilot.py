"""관리자 상담 스위치 (M03_PILOT): the vet-consultation feature works only for guardians who opened the pilot link
(the operator testing it); everyone else sees nothing and the API refuses them. Everything the pilot sends is test
data, and HITL copies of it can never become production knowledge. 수의사 상담 스위치 (M03_ENABLED) stays off."""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from backend.app.config import Settings
from backend.app.db.models_review import KnowledgeCandidate, Reviewer
from backend.app.main import create_app
from backend.app.rag.embeddings import FakeEmbedder
from backend.app.rag.llm import FakeLLM
from backend.app.services import m03
from backend.app.services import review as rsvc
from backend.app.services import worker
from tests.test_api import DOG, IN_SCOPE, _knowledge

H = {"X-TwinVet-Client": "web"}
V = "/api/v1/vet"
CODE = "pilot-code-for-tests"


@pytest.fixture()
def pilot(db, test_db_url):
    _knowledge(db)
    llm = FakeLLM({"classify": IN_SCOPE, "answer": {"response_type": "grounded", "answer": "탈수를 확인하세요.", "used_sources": [1]}})
    # production-like: test vets would normally be hidden; the pilot switch shows them to pilot guardians
    settings = Settings(database_url=test_db_url, cookie_secure=False, worker_threads=0, retrieval_min_score=-1.0,
                        questions_per_minute=100, app_env="production", m03_pilot=True, m03_pilot_code=CODE)
    app = create_app(settings, llm=llm, embedder=FakeEmbedder(), start_worker=False)
    with app.state.Session() as s:
        v, key = m03.create_vet(s, "시험수의", "시험병원", None, is_test=True)

    def guardian():
        c = TestClient(app, base_url="http://localhost:8000")
        c.post("/api/v1/sessions", headers=H)
        c.put("/api/v1/profile", json={"dog": DOG}, headers=H)
        cid = c.post("/api/v1/consultations", json={"dog": DOG}, headers=H).json()["consultation_id"]
        c.post(f"/api/v1/consultations/{cid}/questions", json={"text": "토해요", "client_request_id": uuid.uuid4().hex}, headers=H)
        while True:
            with app.state.Session() as s:
                rid = worker.claim_next(s)
            if rid is None:
                break
            worker.process_run(app.state.Session, settings, llm, app.state.embedder, rid)
        ans = [m for m in c.get(f"/api/v1/consultations/{cid}/messages").json()["messages"] if m["role"] == "assistant"][0]["id"]
        return c, ans

    class E:
        pass

    e = E()
    e.app, e.settings, e.vet_id, e.vet_key, e.guardian = app, settings, str(v.id), key, guardian
    return e


def test_only_the_pilot_guardian_can_use_it(pilot):
    me, my_answer = pilot.guardian()
    other, _ = pilot.guardian()

    for c in (me, other):  # before the pilot link: closed for everyone
        assert c.get("/api/v1/m03/availability").json() == {"enabled": False, "vets": True, "mode": "pilot", "link_days": 90}
        assert c.get("/api/v1/vets").status_code == 404
    assert me.post("/api/v1/m03/pilot", json={"code": "wrong"}, headers=H).status_code == 404
    anon = TestClient(pilot.app, base_url="http://localhost:8000")
    assert anon.post("/api/v1/m03/pilot", json={"code": CODE}, headers=H).status_code == 409  # needs a dog profile
    assert me.post("/api/v1/m03/pilot", json={"code": CODE}, headers=H).status_code == 200
    assert me.get("/api/v1/m03/availability").json()["enabled"] is True

    # the operator (pilot guardian) sees the test vet; the other guardian still gets 404 everywhere
    assert [v["id"] for v in me.get("/api/v1/vets").json()["items"]] == [pilot.vet_id]
    for method, path in (("get", "/api/v1/vets"), ("get", "/api/v1/vet-links"), ("get", "/api/v1/vet-questions"),
                         ("post", "/api/v1/vet-links")):
        r = getattr(other, method)(path, headers=H, **({"json": {"vet_id": pilot.vet_id}} if method == "post" else {}))
        assert r.status_code == 404, path

    # full round trip in pilot; the question is test data
    lid = me.post("/api/v1/vet-links", json={"vet_id": pilot.vet_id}, headers=H).json()["id"]
    vet = TestClient(pilot.app, base_url="http://localhost:8000")
    assert vet.post(f"{V}/login", json={"access_key": pilot.vet_key}, headers=H).status_code == 200
    vet.post(f"{V}/links/{lid}/accept", headers=H)
    q = me.post("/api/v1/vet-questions", json={"link_id": lid, "answer_message_id": my_answer, "quality_use_consent": True},
                headers=H).json()
    assert q["is_test"] is True
    vet.post(f"{V}/questions/{q['id']}/reply", json={"text": "시험 회신"}, headers=H)
    assert me.get("/api/v1/vet-questions").json()["items"][0]["reply"]["body"] == "시험 회신"
    assert vet.get(f"{V}/questions/{q['id']}").json()["is_test"] is True


def test_pilot_hitl_copies_stay_test_and_cannot_be_published(pilot):
    me, ans = pilot.guardian()
    me.post("/api/v1/m03/pilot", json={"code": CODE}, headers=H)
    lid = me.post("/api/v1/vet-links", json={"vet_id": pilot.vet_id}, headers=H).json()["id"]
    vet = TestClient(pilot.app, base_url="http://localhost:8000")
    vet.post(f"{V}/login", json={"access_key": pilot.vet_key}, headers=H)
    vet.post(f"{V}/links/{lid}/accept", headers=H)
    qid = me.post("/api/v1/vet-questions", json={"link_id": lid, "answer_message_id": ans, "quality_use_consent": True},
                  headers=H).json()["id"]
    vet.post(f"{V}/questions/{qid}/reply", json={"text": "시험 회신"}, headers=H)

    with pilot.app.state.Session() as s:
        op_key = rsvc.create_reviewer(s, "operator", "운영자")[1]
    op = TestClient(pilot.app, base_url="http://localhost:8000")
    op.post("/api/v1/review/login", json={"access_key": op_key}, headers=H)
    d = op.get(f"/api/v1/review/m03/pending/{qid}").json()
    item_id = op.post(f"/api/v1/review/m03/pending/{qid}/import", headers=H, json={
        "revision_id": d["revision_id"], "question": d["question"], "ai_answer": d["ai_answer"], "vet_reply": d["vet_reply"]}).json()["item_id"]
    assert op.get(f"/api/v1/review/items/{item_id}").json()["item"]["is_test"] is True
    assert [i["is_test"] for i in op.get("/api/v1/review/items").json()["items"] if i["id"] == item_id] == [True]

    with pilot.app.state.Session() as s:
        reviewer = s.query(Reviewer).filter(Reviewer.display_name == "운영자").one()
        cand = rsvc.create_candidate(s, reviewer, {"item_id": item_id, "kind": "case", "title": "시험 후보",
                                                   "content": "시험 회신에서 만든 후보 내용", "conditions": {}, "evidence": ["x"]})
        assert cand.is_mock is True  # test copy -> treated like mock: never activated in production
        with pytest.raises(rsvc.ReviewError):
            rsvc.activate_candidate(s, cand, FakeEmbedder(), production=True)
        assert s.get(KnowledgeCandidate, cand.id).publish_status == "unapproved"


def test_switches_off_means_nothing_opens(db, test_db_url):
    app = create_app(Settings(database_url=test_db_url, app_env="production", m03_pilot=False, m03_pilot_code=CODE),
                     llm=FakeLLM({}), embedder=FakeEmbedder(), start_worker=False)
    c = TestClient(app, base_url="http://localhost:8000")
    assert c.get("/api/v1/m03/availability").json() == {"enabled": False, "vets": False, "mode": "off", "link_days": 90}
    assert c.post("/api/v1/m03/pilot", json={"code": CODE}, headers=H).status_code == 404
    assert c.get(f"{V}/me").status_code == 404
