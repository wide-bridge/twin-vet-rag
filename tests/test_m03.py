"""M03 guardian–vet link (docs/01 §4-1 completion criterion) on the throwaway test DB: a test guardian picks a vet,
the vet accepts, only the chosen question reaches the vet, the vet's reply reaches that guardian, and no other
guardian or vet can see or touch any of it. Fake LLM/embedder, no network, no cost."""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from backend.app.config import Settings
from backend.app.db.models_m03 import VetAccount, VetQuestion, VetReplyRevision
from backend.app.main import create_app
from backend.app.rag.embeddings import FakeEmbedder
from backend.app.rag.llm import FakeLLM
from backend.app.services import m03, worker
from tests.test_api import DOG, IN_SCOPE, _knowledge

H = {"X-TwinVet-Client": "web"}
V = "/api/v1/vet"


@pytest.fixture()
def m3(db, test_db_url):
    _knowledge(db)
    llm = FakeLLM({"classify": IN_SCOPE, "answer": {"response_type": "grounded", "answer": "탈수를 확인하세요.", "used_sources": [1]}})
    settings = Settings(database_url=test_db_url, cookie_secure=False, worker_threads=0, retrieval_min_score=-1.0,
                        questions_per_minute=100, app_env="test", m03_enabled=True)
    app = create_app(settings, llm=llm, embedder=FakeEmbedder(), start_worker=False)
    with app.state.Session() as s:
        v1, k1 = m03.create_vet(s, "김수의", "튼튼동물병원", "내과", is_test=True)
        v2, k2 = m03.create_vet(s, "이수의", "바른동물병원", None, is_test=True)
        ids = {"v1": str(v1.id), "v2": str(v2.id)}
    keys = {"v1": k1, "v2": k2}

    def drain():
        while True:
            with app.state.Session() as s:
                rid = worker.claim_next(s)
            if rid is None:
                return
            worker.process_run(app.state.Session, settings, llm, app.state.embedder, rid)

    def guardian(breed="말티즈"):
        """A browser: session + saved dog profile + one answered question. Returns (client, answer message id)."""
        c = TestClient(app, base_url="http://localhost:8000")
        assert c.post("/api/v1/sessions", headers=H).status_code in (200, 201)
        dog = {**DOG, "breed": breed, "name": "두부", "health_notes": "심장약 복용"}
        assert c.put("/api/v1/profile", json={"dog": dog}, headers=H).status_code == 200
        cid = c.post("/api/v1/consultations", json={"dog": dog}, headers=H).json()["consultation_id"]
        return c, cid, ask(c, cid)

    def ask(c, cid, q="어제부터 두 번 토했어요"):
        assert c.post(f"/api/v1/consultations/{cid}/questions", json={"text": q, "client_request_id": uuid.uuid4().hex},
                      headers=H).status_code == 202
        drain()
        msgs = c.get(f"/api/v1/consultations/{cid}/messages").json()["messages"]
        return [m for m in msgs if m["role"] == "assistant"][-1]["id"]

    def vet(name):
        c = TestClient(app, base_url="http://localhost:8000")
        r = c.post(f"{V}/login", json={"access_key": keys[name]}, headers=H)
        assert r.status_code == 200, r.text
        return c

    class E:
        pass

    e = E()
    e.app, e.settings, e.ids, e.keys, e.guardian, e.ask, e.vet = app, settings, ids, keys, guardian, ask, vet
    return e


def _connect(e, g, vet_client, vet_name="v1"):
    link = g.post("/api/v1/vet-links", json={"vet_id": e.ids[vet_name]}, headers=H).json()
    assert vet_client.post(f"{V}/links/{link['id']}/accept", headers=H).status_code == 200
    return link["id"]


def test_round_trip_only_the_chosen_question_reaches_the_vet(m3):
    g, cid, answer = m3.guardian()
    other_answer = m3.ask(g, cid, "산책은 해도 되나요?")  # in the same consultation, never sent
    v1 = m3.vet("v1")

    # ① choose a vet and request; ② nothing can be sent before the vet accepts
    vets = g.get("/api/v1/vets").json()["items"]
    assert {v["id"] for v in vets} == set(m3.ids.values())
    link = g.post("/api/v1/vet-links", json={"vet_id": m3.ids["v1"]}, headers=H)
    assert link.status_code == 201 and link.json()["status"] == "requested"
    lid = link.json()["id"]
    assert g.post("/api/v1/vet-links", json={"vet_id": m3.ids["v1"]}, headers=H).json()["error"]["code"] == "link_exists"
    # ③ the question can go with the pending request: held, invisible to the vet until it accepts
    sent = g.post("/api/v1/vet-questions", headers=H, json={
        "link_id": lid, "answer_message_id": answer, "note_to_vet": "어제 물어본 사료 이야기예요",
        "shared_fields": ["breed", "age"], "quality_use_consent": False})
    assert sent.status_code == 201, sent.text
    q = sent.json()
    assert q["status"] == "sent" and q["stage"] == "held" and q["access_token"]
    pending = v1.get(f"{V}/links").json()["items"]
    assert len(pending) == 1 and set(pending[0]) >= {"id", "status", "guardian_ref", "requested_at"}
    assert not any(k in pending[0] for k in ("snapshot", "dog", "question", "breed"))  # no content before acceptance
    assert v1.get(f"{V}/questions").json()["items"] == []
    assert v1.get(f"{V}/questions/{q['id']}").status_code == 404  # held: not even by id
    acc = v1.post(f"{V}/links/{lid}/accept", headers=H).json()
    assert acc["status"] == "accepted" and acc["expires_at"]  # 주치수의사 for VET_LINK_DAYS
    assert g.get(f"/api/v1/vet-questions/{q['id']}").json()["stage"] == "delivered"
    snap = q["snapshot"]
    assert snap["question"] == "어제부터 두 번 토했어요" and snap["ai_answer"] == "탈수를 확인하세요."
    assert snap["note_to_vet"] == "어제 물어본 사료 이야기예요" and snap["dog"] == {"breed": "말티즈", "age": DOG["age_months"]}
    assert "두부" not in str(snap) and "심장약" not in str(snap)  # name / health notes not chosen -> not shared
    assert g.post("/api/v1/vet-questions", json={"link_id": lid, "answer_message_id": answer}, headers=H).status_code == 409

    # ④ the vet sees that question only (not the other answer of the same consultation) and replies
    items = v1.get(f"{V}/questions").json()["items"]
    assert [i["id"] for i in items] == [q["id"]]
    detail = v1.get(f"{V}/questions/{q['id']}").json()
    assert detail["snapshot"]["question"] == "어제부터 두 번 토했어요" and "산책" not in str(detail)
    assert other_answer not in str(detail)
    assert g.get(f"/api/v1/vet-questions/{q['id']}").json()["stage"] == "seen"  # the vet opened it
    assert v1.put(f"{V}/questions/{q['id']}/draft", json={"text": "초안"}, headers=H).json()["reply_draft"] == "초안"
    assert g.get(f"/api/v1/vet-questions/{q['id']}").json()["stage"] == "drafting"
    assert v1.put(f"{V}/questions/{q['id']}/draft", json={"text": "수정한 초안"}, headers=H).json()["reply_draft"] == "수정한 초안"
    r = v1.post(f"{V}/questions/{q['id']}/reply", json={"text": "탈수 여부를 먼저 보세요."}, headers=H).json()
    assert r["status"] == "answered" and r["reply"]["body"] == "탈수 여부를 먼저 보세요." and r["reply_draft"] is None
    assert v1.post(f"{V}/questions/{q['id']}/reply", json={"text": "또"}, headers=H).status_code == 409
    c = v1.post(f"{V}/questions/{q['id']}/correction", json={"text": "탈수와 잇몸 색을 보세요."}, headers=H).json()
    assert c["reply"]["corrected"] and [h["kind"] for h in c["reply"]["history"]] == ["original", "correction"]

    # ⑤ the guardian sees status + latest reply (+ history); the link opens the same question without cookies
    mine = g.get("/api/v1/vet-questions").json()["items"]
    assert len(mine) == 1 and mine[0]["status"] == "answered" and mine[0]["answer_message_id"] == answer
    rep = mine[0]["reply"]
    assert rep["body"] == "탈수와 잇몸 색을 보세요." and rep["corrected"]
    assert rep["history"][0]["body"] == "탈수 여부를 먼저 보세요."  # the original is kept unchanged
    anon = TestClient(m3.app, base_url="http://localhost:8000")
    by_link = anon.get(f"/api/v1/vet-questions/by-token/{q['access_token']}")
    assert by_link.status_code == 200 and by_link.json()["id"] == q["id"]
    assert anon.get("/api/v1/vet-questions/by-token/wrong-token").status_code == 404
    new_tok = g.post(f"/api/v1/vet-questions/{q['id']}/link", headers=H).json()["access_token"]
    assert anon.get(f"/api/v1/vet-questions/by-token/{q['access_token']}").status_code == 404  # old link revoked
    assert anon.get(f"/api/v1/vet-questions/by-token/{new_tok}").status_code == 200


def test_other_guardians_and_vets_cannot_reach_anything(m3):
    a, _, answer_a = m3.guardian()
    b, _, answer_b = m3.guardian("푸들")
    v1, v2 = m3.vet("v1"), m3.vet("v2")
    lid = _connect(m3, a, v1)
    q = a.post("/api/v1/vet-questions", json={"link_id": lid, "answer_message_id": answer_a}, headers=H).json()
    qid = q["id"]

    # another guardian: own lists empty, A's link/question/answer all 404
    assert b.get("/api/v1/vet-links").json()["items"] == [] and b.get("/api/v1/vet-questions").json()["items"] == []
    assert b.get(f"/api/v1/vet-questions/{qid}").status_code == 404
    assert b.post(f"/api/v1/vet-questions/{qid}/cancel", headers=H).status_code == 404
    assert b.patch(f"/api/v1/vet-questions/{qid}", json={"quality_use_consent": True}, headers=H).status_code == 404
    assert b.post(f"/api/v1/vet-questions/{qid}/link", headers=H).status_code == 404
    assert b.delete(f"/api/v1/vet-questions/{qid}", headers=H).status_code == 404
    assert b.delete(f"/api/v1/vet-links/{lid}", headers=H).status_code == 404
    assert b.post("/api/v1/vet-questions", json={"link_id": lid, "answer_message_id": answer_b}, headers=H).status_code == 404
    lid_b = _connect(m3, b, v1)
    # B's own accepted link, but A's answer: refused
    assert b.post("/api/v1/vet-questions", json={"link_id": lid_b, "answer_message_id": answer_a}, headers=H).status_code == 404
    # A cannot send B's answer either, even through A's own link
    assert a.post("/api/v1/vet-questions", json={"link_id": lid, "answer_message_id": answer_b}, headers=H).status_code == 404

    # another vet: nothing listed, everything 404
    assert v2.get(f"{V}/links").json()["items"] == [] and v2.get(f"{V}/questions").json()["items"] == []
    assert v2.get(f"{V}/questions/{qid}").status_code == 404
    assert v2.put(f"{V}/questions/{qid}/draft", json={"text": "x"}, headers=H).status_code == 404
    assert v2.post(f"{V}/questions/{qid}/reply", json={"text": "x"}, headers=H).status_code == 404
    assert v2.post(f"{V}/links/{lid}/end", headers=H).status_code == 404
    assert v2.post(f"{V}/links/{lid}/accept", headers=H).status_code == 404

    # no cookies at all
    anon = TestClient(m3.app, base_url="http://localhost:8000")
    assert anon.get(f"{V}/questions").status_code == 401
    assert anon.get("/api/v1/vet-questions").status_code == 409  # no dog profile -> nothing to show
    assert anon.post("/api/v1/vet-links", json={"vet_id": m3.ids["v1"]}, headers=H).status_code == 409
    # missing CSRF header
    assert a.post(f"/api/v1/vet-questions/{qid}/cancel").status_code == 403

    # a deactivated vet is logged out everywhere and hidden from guardians
    with m3.app.state.Session() as s:
        s.get(VetAccount, uuid.UUID(m3.ids["v2"])).active = False
        s.commit()
    assert v2.get(f"{V}/me").status_code == 401
    assert m3.ids["v2"] not in {v["id"] for v in a.get("/api/v1/vets").json()["items"]}
    assert TestClient(m3.app).post(f"{V}/login", json={"access_key": m3.keys["v2"]}, headers=H).status_code == 401


def test_cancel_end_delete_and_frozen_copy(m3):
    g, cid, answer = m3.guardian()
    second = m3.ask(g, cid, "물은 얼마나 줘야 하나요?")
    v1 = m3.vet("v1")
    lid = _connect(m3, g, v1)
    q1 = g.post("/api/v1/vet-questions", json={"link_id": lid, "answer_message_id": answer}, headers=H).json()
    q2 = g.post("/api/v1/vet-questions", json={"link_id": lid, "answer_message_id": second}, headers=H).json()

    # the copy is frozen: editing the profile or deleting the consultation does not change what the vet received
    g.put("/api/v1/profile", json={"dog": {**DOG, "breed": "진돗개"}}, headers=H)
    assert g.delete(f"/api/v1/consultations/{cid}", headers=H).status_code == 204
    d = v1.get(f"{V}/questions/{q1['id']}").json()
    assert d["snapshot"]["dog"]["breed"] == "말티즈" and d["snapshot"]["question"] == "어제부터 두 번 토했어요"

    # cancel before reply; a cancelled question shows no content to the vet and cannot be answered
    assert g.post(f"/api/v1/vet-questions/{q1['id']}/cancel", headers=H).json()["status"] == "cancelled"
    cancelled = v1.get(f"{V}/questions/{q1['id']}").json()
    assert cancelled["status"] == "cancelled" and "snapshot" not in cancelled and "question_preview" not in cancelled
    assert v1.post(f"{V}/questions/{q1['id']}/reply", json={"text": "x"}, headers=H).status_code == 409

    # consent can be withdrawn at any time
    assert g.patch(f"/api/v1/vet-questions/{q2['id']}", json={"quality_use_consent": True}, headers=H).json()["quality_use_consent"]
    assert not g.patch(f"/api/v1/vet-questions/{q2['id']}", json={"quality_use_consent": False}, headers=H).json()["quality_use_consent"]

    # ending the link cancels pending questions and blocks new ones
    assert g.delete(f"/api/v1/vet-links/{lid}", headers=H).json()["status"] == "ended"
    after = g.get(f"/api/v1/vet-questions/{q2['id']}").json()
    assert after["status"] == "cancelled" and after["cancelled_reason"] == "link_ended"
    # a new link to the same vet can be requested again after the old one ended
    assert g.post("/api/v1/vet-links", json={"vet_id": m3.ids["v1"]}, headers=H).status_code == 201

    # delete: gone for both sides, with its reply history
    assert g.delete(f"/api/v1/vet-questions/{q2['id']}", headers=H).status_code == 204
    assert v1.get(f"{V}/questions/{q2['id']}").status_code == 404
    with m3.app.state.Session() as s:
        assert s.get(VetQuestion, uuid.UUID(q2["id"])) is None


def test_vet_actions_do_not_extend_retention_and_profile_delete_removes_everything(m3):
    g, _, answer = m3.guardian()
    v1 = m3.vet("v1")
    lid = _connect(m3, g, v1)
    qid = g.post("/api/v1/vet-questions", json={"link_id": lid, "answer_message_id": answer}, headers=H).json()["id"]
    with m3.app.state.Session() as s:
        s.execute(text("UPDATE dog_profile SET expires_at = now() + interval '3 days'"))
        s.commit()
        before = s.execute(text("SELECT expires_at FROM dog_profile")).scalar()
    v1.get(f"{V}/questions/{qid}")
    v1.post(f"{V}/questions/{qid}/reply", json={"text": "답변"}, headers=H)
    v1.post(f"{V}/questions/{qid}/correction", json={"text": "정정"}, headers=H)
    with m3.app.state.Session() as s:
        assert s.execute(text("SELECT expires_at FROM dog_profile")).scalar() == before  # vet use never extends
    g.get("/api/v1/vet-questions")
    with m3.app.state.Session() as s:
        assert s.execute(text("SELECT expires_at FROM dog_profile")).scalar() > before  # guardian use does

    assert g.delete("/api/v1/profile", headers=H).status_code == 204
    with m3.app.state.Session() as s:
        for t in ("vet_link", "vet_question", "vet_reply_revision"):
            assert s.execute(text(f"SELECT count(*) FROM {t}")).scalar() == 0, t
    assert v1.get(f"{V}/questions").json()["items"] == []


def test_switch_off_hides_every_m03_route_but_availability(db, test_db_url):
    app = create_app(Settings(database_url=test_db_url, app_env="test", m03_enabled=False), llm=FakeLLM({}),
                     embedder=FakeEmbedder(), start_worker=False)
    c = TestClient(app, base_url="http://localhost:8000")
    assert c.get("/api/v1/m03/availability").json()["enabled"] is False
    for method, path in (("get", "/api/v1/vets"), ("get", "/api/v1/vet-links"), ("get", "/api/v1/vet-questions"),
                         ("post", f"{V}/login"), ("get", f"{V}/me"), ("get", f"{V}/questions")):
        r = getattr(c, method)(path, headers=H, **({"json": {"access_key": "x" * 10}} if method == "post" else {}))
        assert r.status_code == 404, (path, r.status_code)


def test_vet_host_answers_only_the_vet_api(db, test_db_url):
    s = Settings(database_url=test_db_url, app_env="test", m03_enabled=True, vet_host="vet.example.test",
                 review_host="hitl-review.example.test")
    app = create_app(s, llm=FakeLLM({}), embedder=FakeEmbedder(), start_worker=False)
    vet_host = TestClient(app, base_url="https://vet.example.test")
    guardian_host = TestClient(app, base_url="https://twinvet.example.test")
    review_host = TestClient(app, base_url="https://hitl-review.example.test")
    assert vet_host.get(f"{V}/me").status_code == 401  # reaches the vet API (not logged in)
    assert vet_host.get("/api/v1/vets").status_code == 404 and vet_host.get("/api/v1/review/me").status_code == 404
    assert vet_host.get(f"{V}/me").headers["x-robots-tag"] == "noindex, nofollow"
    assert guardian_host.get(f"{V}/me").status_code == 404 and review_host.get(f"{V}/me").status_code == 404
    assert guardian_host.get("/api/v1/vets").status_code == 200
    for c in (vet_host, guardian_host, review_host):  # the switch is readable from every app
        assert c.get("/api/v1/m03/availability").json()["enabled"] is True


def test_cancelled_questions_close_their_link(m3):
    """Codex 2026-11-14 ①: a cancelled question (by the guardian or by ending the link) is not readable by link."""
    g, cid, answer = m3.guardian()
    second = m3.ask(g, cid, "물은 얼마나 줘야 하나요?")
    v1 = m3.vet("v1")
    lid = _connect(m3, g, v1)
    anon = TestClient(m3.app, base_url="http://localhost:8000")
    q1 = g.post("/api/v1/vet-questions", json={"link_id": lid, "answer_message_id": answer}, headers=H).json()
    q2 = g.post("/api/v1/vet-questions", json={"link_id": lid, "answer_message_id": second}, headers=H).json()
    assert anon.get(f"/api/v1/vet-questions/by-token/{q1['access_token']}").status_code == 200

    g.post(f"/api/v1/vet-questions/{q1['id']}/cancel", headers=H)
    assert anon.get(f"/api/v1/vet-questions/by-token/{q1['access_token']}").status_code == 404
    re = g.post(f"/api/v1/vet-questions/{q1['id']}/link", headers=H)
    assert re.status_code == 409 and re.json()["error"]["code"] == "question_closed"

    g.delete(f"/api/v1/vet-links/{lid}", headers=H)  # q2 is cancelled with the link
    assert anon.get(f"/api/v1/vet-questions/by-token/{q2['access_token']}").status_code == 404
    assert g.post(f"/api/v1/vet-questions/{q2['id']}/link", headers=H).status_code == 409
    with m3.app.state.Session() as s:
        assert s.execute(text("SELECT count(*) FROM vet_question WHERE access_token_hash IS NOT NULL")).scalar() == 0


def test_expired_profile_is_closed_to_the_vet_before_cleanup(m3):
    """Codex 2026-11-14 ②: once the guardian's profile expires the vet can no longer list, read or answer it,
    even though the cleanup job has not deleted the rows yet."""
    g, cid, answer = m3.guardian()
    second = m3.ask(g, cid, "물은 얼마나 줘야 하나요?")
    v1 = m3.vet("v1")
    lid = _connect(m3, g, v1)
    q1 = g.post("/api/v1/vet-questions", json={"link_id": lid, "answer_message_id": answer}, headers=H).json()
    q2 = g.post("/api/v1/vet-questions", json={"link_id": lid, "answer_message_id": second}, headers=H).json()
    v1.post(f"{V}/questions/{q2['id']}/reply", json={"text": "답변"}, headers=H)
    b, _, answer_b = m3.guardian("푸들")  # another guardian, still alive
    lid_b = _connect(m3, b, v1)
    qb = b.post("/api/v1/vet-questions", json={"link_id": lid_b, "answer_message_id": answer_b}, headers=H).json()

    with m3.app.state.Session() as s:
        s.execute(text("UPDATE dog_profile SET expires_at = now() - interval '1 minute' WHERE id = "
                       "(SELECT profile_id FROM vet_question WHERE id = :q)"), {"q": q1["id"]})
        s.commit()
        assert s.execute(text("SELECT count(*) FROM vet_question")).scalar() == 3  # rows still there (no cleanup)

    assert [i["id"] for i in v1.get(f"{V}/questions").json()["items"]] == [qb["id"]]
    assert [l["id"] for l in v1.get(f"{V}/links").json()["items"]] == [lid_b]
    for qid in (q1["id"], q2["id"]):
        assert v1.get(f"{V}/questions/{qid}").status_code == 404
        assert v1.put(f"{V}/questions/{qid}/draft", json={"text": "x"}, headers=H).status_code == 404
        assert v1.post(f"{V}/questions/{qid}/reply", json={"text": "x"}, headers=H).status_code == 404
        assert v1.post(f"{V}/questions/{qid}/correction", json={"text": "x"}, headers=H).status_code == 404
    assert v1.post(f"{V}/links/{lid}/end", headers=H).status_code == 404
    anon = TestClient(m3.app, base_url="http://localhost:8000")
    assert anon.get(f"/api/v1/vet-questions/by-token/{q1['access_token']}").status_code == 404
    assert g.get("/api/v1/vet-questions").status_code == 409  # the guardian's own profile is gone too
    assert v1.get(f"{V}/questions/{qb['id']}").status_code == 200  # other guardians unaffected


def test_declined_request_cancels_held_questions_and_link_expires(m3):
    """주치수의사 rules: a declined request never shows its held question to the vet; an accepted link expires after
    VET_LINK_DAYS, after which questions need a new request (the vet stays a plain 수의사 until it accepts again)."""
    g, cid, answer = m3.guardian()
    v1 = m3.vet("v1")
    lid = g.post("/api/v1/vet-links", json={"vet_id": m3.ids["v1"]}, headers=H).json()["id"]
    q = g.post("/api/v1/vet-questions", json={"link_id": lid, "answer_message_id": answer}, headers=H).json()
    assert v1.post(f"{V}/links/{lid}/decline", headers=H).json()["status"] == "declined"
    after = g.get(f"/api/v1/vet-questions/{q['id']}").json()
    assert after["stage"] == "cancelled" and after["cancelled_reason"] == "link_declined"
    assert v1.get(f"{V}/questions").json()["items"] == [] and v1.get(f"{V}/questions/{q['id']}").status_code == 404

    lid2 = _connect(m3, g, v1)
    with m3.app.state.Session() as s:
        s.execute(text("UPDATE vet_link SET expires_at = now() - interval '1 day' WHERE id = :i"), {"i": lid2})
        s.commit()
    assert [l["status"] for l in g.get("/api/v1/vet-links").json()["items"] if l["id"] == lid2] == ["expired"]
    second = m3.ask(g, cid, "물은 얼마나?")
    r = g.post("/api/v1/vet-questions", json={"link_id": lid2, "answer_message_id": second}, headers=H)
    assert r.status_code == 409 and r.json()["error"]["code"] == "link_expired"
    again = g.post("/api/v1/vet-links", json={"vet_id": m3.ids["v1"]}, headers=H)  # re-request after expiry
    assert again.status_code == 201 and again.json()["status"] == "requested"
