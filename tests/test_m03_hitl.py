"""M03 -> HITL (docs/01 §4-1 '②→③ 연결 규칙', 'HITL 이관 조건'): operator stats/import, and the three conditions that keep
withdrawal, correction and deletion consistent into HITL: import vs withdrawal under one row lock, correction after
import flags the copy without touching labels, removing a copy blocks/unpublishes knowledge made from it."""
from __future__ import annotations

import threading
import time
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from backend.app.db.models import Document
from backend.app.db.models_review import KnowledgeCandidate, ReviewItem, ReviewLabel
from backend.app.services import profile as profile_svc
from backend.app.services import review as rsvc
from tests.test_m03 import H, V, _connect, m3  # noqa: F401  (fixture)

R = "/api/v1/review"


@pytest.fixture()
def hx(m3):  # noqa: F811
    with m3.app.state.Session() as s:
        op_key = rsvc.create_reviewer(s, "operator", "운영자")[1]
        vet_key = rsvc.create_reviewer(s, "vet", "검토수의")[1]

    def reviewer(key):
        c = TestClient(m3.app, base_url="http://localhost:8000")
        assert c.post(f"{R}/login", json={"access_key": key}, headers=H).status_code == 200
        return c

    m3.op, m3.rvet = reviewer(op_key), reviewer(vet_key)

    def answered(consent=True, text_reply="탈수를 먼저 확인하세요."):
        g, cid, answer = m3.guardian()
        v1 = m3.vet("v1")
        lid = _connect(m3, g, v1)
        q = g.post("/api/v1/vet-questions", headers=H, json={
            "link_id": lid, "answer_message_id": answer, "note_to_vet": "두부 엄마 010-1234-5678 이에요",
            "shared_fields": ["name", "breed", "age", "health_notes"], "quality_use_consent": consent}).json()
        v1.post(f"{V}/questions/{q['id']}/reply", json={"text": text_reply}, headers=H)
        return g, v1, q["id"]

    m3.answered = answered
    return m3


def _import(op, qid, **over):
    d = op.get(f"{R}/m03/pending/{qid}").json()
    body = {"revision_id": d["revision_id"], "question": d["question"], "ai_answer": d["ai_answer"],
            "note_to_vet": "(연락처 삭제) 보호자 메모", "vet_reply": d["vet_reply"], **over}
    return op.post(f"{R}/m03/pending/{qid}/import", json=body, headers=H)


def test_queue_shows_only_consented_answered_questions_and_import_makes_a_linked_copy(hx):
    g1, _, q_yes = hx.answered(consent=True)
    g2, _, q_no = hx.answered(consent=False)
    op = hx.op

    pend = op.get(f"{R}/m03/pending").json()["items"]
    assert [p["id"] for p in pend] == [q_yes]
    assert op.get(f"{R}/m03/pending/{q_no}").status_code == 409  # no consent: the body is never shown
    stats = op.get(f"{R}/m03/stats").json()
    assert stats["questions"]["answered"] == 2 and stats["consent"] == {"yes": 1, "total": 2, "rate": 0.5}
    assert stats["import"]["pending"] == 1 and "토했어요" not in str(stats)  # numbers only
    assert hx.rvet.get(f"{R}/m03/stats").status_code == 403  # vet reviewers do not see operator views

    d = op.get(f"{R}/m03/pending/{q_yes}").json()
    assert "name" not in d["dog"] and "health_notes" not in d["dog"]  # never carried into HITL
    r = _import(op, q_yes, question="어제부터 두 번 토했어요 (정리)")
    assert r.status_code == 201, r.text
    assert _import(op, q_yes).status_code == 409  # same reply version twice
    assert op.get(f"{R}/m03/pending").json()["items"] == []

    item_id = r.json()["item_id"]
    detail = op.get(f"{R}/items/{item_id}").json()["item"]
    assert detail["origin"] == "consultation" and detail["question"] == "어제부터 두 번 토했어요 (정리)"
    assert detail["vet_reference"] == "탈수를 먼저 확인하세요." and "010-1234" not in str(detail)
    assert hx.rvet.get(f"{R}/items/{item_id}").status_code == 200  # reviewers label it like any bundle
    with hx.app.state.Session() as s:
        it = s.get(ReviewItem, uuid.UUID(item_id))
        assert str(it.vet_question_id) == q_yes and it.vet_reply_revision_id is not None


def test_withdrawal_and_import_cannot_cross(hx, test_db_url):
    """Condition ①: import re-checks consent under the question row lock; withdrawal takes the same lock."""
    g, _, qid = hx.answered()
    op = hx.op
    # withdrawal committed while the import waits on the lock -> the import is refused
    eng = create_engine(test_db_url)
    conn = eng.connect()
    tx = conn.begin()
    conn.execute(text("SELECT id FROM vet_question WHERE id = :q FOR UPDATE"), {"q": qid})
    conn.execute(text("UPDATE vet_question SET quality_use_consent = false WHERE id = :q"), {"q": qid})
    d = op.get(f"{R}/m03/pending/{qid}")  # read without lock: still listed before the withdrawal commits
    assert d.status_code == 200
    out = {}
    body = {"revision_id": d.json()["revision_id"], "question": "q", "ai_answer": "a", "vet_reply": "r"}
    t = threading.Thread(target=lambda: out.setdefault("r", op.post(f"{R}/m03/pending/{qid}/import", json=body, headers=H)))
    t.start()
    time.sleep(0.5)
    assert t.is_alive()  # blocked on the row lock
    tx.commit()
    t.join(10)
    conn.close()
    eng.dispose()
    assert out["r"].status_code == 409 and out["r"].json()["error"]["code"] == "not_importable"
    with hx.app.state.Session() as s:
        assert s.execute(text("SELECT count(*) FROM review_item WHERE origin='consultation'")).scalar() == 0

    # import first, then withdrawal -> the copy and its labels are removed
    g2, _, q2 = hx.answered()
    item_id = _import(op, q2).json()["item_id"]
    with hx.app.state.Session() as s:
        it = s.get(ReviewItem, uuid.UUID(item_id))
        rsvc.save_label(s, it, _reviewer(s, "검토수의"), {"accuracy": "appropriate"})
    assert g2.patch(f"/api/v1/vet-questions/{q2}", json={"quality_use_consent": False}, headers=H).status_code == 200
    with hx.app.state.Session() as s:
        assert s.get(ReviewItem, uuid.UUID(item_id)) is None
        assert s.execute(text("SELECT count(*) FROM review_label WHERE item_id = :i"), {"i": item_id}).scalar() == 0
    assert op.get(f"{R}/m03/pending/{q2}").status_code == 409


def _reviewer(s, name):
    from backend.app.db.models_review import Reviewer
    return s.query(Reviewer).filter(Reviewer.display_name == name).one()


def test_correction_after_import_flags_the_copy_and_keeps_labels(hx):
    """Condition ②."""
    g, v1, qid = hx.answered()
    op = hx.op
    item_id = _import(op, qid).json()["item_id"]
    with hx.app.state.Session() as s:
        rsvc.save_label(s, s.get(ReviewItem, uuid.UUID(item_id)), _reviewer(s, "검토수의"), {"accuracy": "appropriate", "note": "원래 판단"})
    v1.post(f"{V}/questions/{qid}/correction", json={"text": "탈수와 잇몸 색을 같이 보세요."}, headers=H)

    detail = op.get(f"{R}/items/{item_id}").json()["item"]
    assert detail["needs_recheck"] is True and detail["vet_reference"] == "탈수를 먼저 확인하세요."  # not overwritten
    listed = [i for i in op.get(f"{R}/items").json()["items"] if i["id"] == item_id]
    assert listed and listed[0]["needs_recheck"]
    with hx.app.state.Session() as s:
        lab = s.query(ReviewLabel).filter(ReviewLabel.item_id == uuid.UUID(item_id)).one()
        assert lab.note == "원래 판단" and lab.accuracy == "appropriate"
    pend = op.get(f"{R}/m03/pending").json()["items"]
    assert [(p["id"], p["corrected"], p["earlier_imports"]) for p in pend] == [(qid, True, 1)]
    second = _import(op, qid)
    assert second.status_code == 201 and second.json()["item_id"] != item_id  # new copy for the new version


def test_removing_a_copy_blocks_and_unpublishes_knowledge_made_from_it(hx):
    """Condition ③: approved candidate -> unapproved + blocked; published one -> document out of search."""
    g, _, qid = hx.answered()
    item_id = uuid.UUID(_import(hx.op, qid).json()["item_id"])
    with hx.app.state.Session() as s:
        doc = Document(dataset_code="vetreview", source_record_id=f"candidate-{uuid.uuid4()}", document_type="vet_knowledge",
                       title="발행된 지식", source_name="t", status="active")
        s.add(doc)
        s.flush()
        active = KnowledgeCandidate(item_id=item_id, kind="case", title="발행", content="x", content_hash="h1", is_mock=False,
                                    publish_status="active", approved_hash="h1", document_id=doc.id)
        approved = KnowledgeCandidate(item_id=item_id, kind="case", title="승인", content="y", content_hash="h2", is_mock=False,
                                      publish_status="approved", approved_hash="h2")
        s.add_all([active, approved])
        s.commit()
        ids, doc_id = (active.id, approved.id), doc.id

    assert g.delete(f"/api/v1/vet-questions/{qid}", headers=H).status_code == 204
    with hx.app.state.Session() as s:
        assert s.get(ReviewItem, item_id) is None
        assert s.get(Document, doc_id).status == "inactive"  # out of search
        for cid in ids:
            c = s.get(KnowledgeCandidate, cid)
            assert c.publish_status == "unapproved" and c.approved_hash is None and c.source_withdrawn_at is not None
            assert c.item_id is None
        c = s.get(KnowledgeCandidate, ids[1])
        with pytest.raises(rsvc.ReviewError) as e:
            rsvc.approve_candidate(s, c, _reviewer(s, "검토수의"), production=False)
        assert e.value.code == "source_withdrawn"
        ev = s.execute(text("SELECT count(*) FROM review_event WHERE event='source_withdrawn'")).scalar()
        assert ev == 2
        # rewritten from what remains -> the block is lifted (normal approval rules apply again)
        rsvc.update_candidate(s, c, _reviewer(s, "검토수의"), {"kind": "case", "title": "승인", "content": "남은 근거로 다시 쓴 내용",
                                                            "conditions": {}, "evidence": ["남은 원천 근거"]})
        assert c.source_withdrawn_at is None


def test_profile_delete_and_expiry_remove_copies_and_expired_ones_are_hidden_first(hx):
    g, _, qid = hx.answered()
    item_a = _import(hx.op, qid).json()["item_id"]
    g2, _, q2 = hx.answered()
    item_b = _import(hx.op, q2).json()["item_id"]

    assert g.delete("/api/v1/profile", headers=H).status_code == 204
    with hx.app.state.Session() as s:
        assert s.get(ReviewItem, uuid.UUID(item_a)) is None

    with hx.app.state.Session() as s:
        s.execute(text("UPDATE dog_profile SET expires_at = now() - interval '1 minute' WHERE id = "
                       "(SELECT profile_id FROM vet_question WHERE id = :q)"), {"q": q2})
        s.commit()
    assert hx.op.get(f"{R}/items/{item_b}").status_code == 404  # hidden before cleanup
    assert item_b not in [i["id"] for i in hx.op.get(f"{R}/items").json()["items"]]
    with hx.app.state.Session() as s:
        assert profile_svc.purge_expired_profiles(s) == 1
        assert s.get(ReviewItem, uuid.UUID(item_b)) is None
        assert s.execute(text("SELECT count(*) FROM vet_question")).scalar() == 0


def test_operator_status_works_while_m03_is_closed(db, test_db_url):
    """운영자 현황 is visible in hitl-review even before M03 opens to guardians (all zero); vets still get 403."""
    from backend.app.config import Settings
    from backend.app.main import create_app
    from backend.app.rag.embeddings import FakeEmbedder
    from backend.app.rag.llm import FakeLLM
    app = create_app(Settings(database_url=test_db_url, app_env="test", m03_enabled=False, cookie_secure=False),
                     llm=FakeLLM({}), embedder=FakeEmbedder(), start_worker=False)
    with app.state.Session() as s:
        key = rsvc.create_reviewer(s, "operator", "운영자")[1]
    c = TestClient(app, base_url="http://localhost:8000")
    assert c.post(f"{R}/login", json={"access_key": key}, headers=H).status_code == 200
    st = c.get(f"{R}/m03/stats")
    assert st.status_code == 200 and st.json()["questions"] == {"sent": 0, "answered": 0, "cancelled": 0}
    assert c.get(f"{R}/m03/pending").json() == {"items": []}
    assert c.get("/api/v1/vets").status_code == 404  # guardian M03 API stays closed
