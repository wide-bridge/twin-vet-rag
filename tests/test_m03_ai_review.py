"""M03 vet review of the AI answer ('HITL 우리', docs/06 §20): the vet judges the AI answer the guardian received and
sends it to TwinVet only. Independent of the reply, never shown to the guardian, text kept only with the guardian's
quality-use consent (otherwise verdict + safety flag only), and it can reach HITL through the operator import."""
from __future__ import annotations

from tests.test_m03 import H, V, _connect, m3  # noqa: F401  (fixture)
from tests.test_m03_hitl import R, hx  # noqa: F401  (fixture)


def _sent(e, consent=True, vet="v1"):
    g, _, answer = e.guardian()
    v = e.vet(vet)
    lid = _connect(e, g, v, vet)
    q = g.post("/api/v1/vet-questions", headers=H, json={
        "link_id": lid, "answer_message_id": answer, "note_to_vet": None,
        "shared_fields": ["breed", "age"], "quality_use_consent": consent}).json()
    return g, v, q["id"]


def _review(v, qid, **body):
    return v.put(f"{V}/questions/{qid}/ai-review", json={"verdict": "needs_fix", "safety_issue": False, **body}, headers=H)


def test_review_without_reply_goes_to_twinvet_only_and_can_be_imported(hx):
    g, v, qid = _sent(hx)
    r = _review(v, qid, corrected_answer="구토가 반복되면 바로 내원하세요.", comment="응급 경고가 빠짐", safety_issue=True)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["status"] == "sent" and d["ai_reviewed"] is True  # no reply: the question stays open
    assert d["ai_review"]["verdict"] == "needs_fix" and d["ai_review"]["safety_issue"] is True

    # the guardian never sees the review
    gv = g.get(f"/api/v1/vet-questions/{qid}").json()
    assert "ai_review" not in str(gv) and "응급 경고가 빠짐" not in str(gv) and "needs_fix" not in str(gv)

    # operator queue: review only, no reply
    pend = hx.op.get(f"{R}/m03/pending").json()["items"]
    assert len(pend) == 1 and pend[0]["revision_id"] is None and pend[0]["verdict"] == "needs_fix" and pend[0]["safety_issue"]
    det = hx.op.get(f"{R}/m03/pending/{qid}").json()
    assert det["vet_reply"] is None and det["review"]["comment"] == "응급 경고가 빠짐"
    body = {"revision_id": None, "review_id": det["review"]["id"], "question": det["question"], "ai_answer": det["ai_answer"],
            "note_to_vet": None, "vet_reply": "[보완 필요·안전] 구토가 반복되면 바로 내원하세요."}
    imp = hx.op.post(f"{R}/m03/pending/{qid}/import", json=body, headers=H)
    assert imp.status_code == 201, imp.text
    item = hx.op.get(f"{R}/items/{imp.json()['item_id']}").json()["item"]
    assert item["versions"]["vet_verdict"] == "needs_fix" and item["versions"]["vet_safety_issue"] is True
    assert hx.op.get(f"{R}/m03/pending").json()["items"] == []

    st = hx.op.get(f"{R}/m03/stats").json()["ai_review"]
    assert st == {"reviewed": 1, "correct": 0, "needs_fix": 1, "different": 0, "safety_issue": 1}


def test_without_consent_only_verdict_and_safety_are_kept(hx):
    g, v, qid = _sent(hx, consent=False)
    r = _review(v, qid, corrected_answer="고친 답변")
    assert r.status_code == 409 and r.json()["error"]["code"] == "consent_required"
    r = _review(v, qid, verdict="different", safety_issue=True)
    assert r.status_code == 200 and r.json()["quality_use_consent"] is False
    assert r.json()["ai_review"]["corrected_answer"] is None
    assert hx.op.get(f"{R}/m03/pending").json()["items"] == []  # never in the import queue
    assert hx.op.get(f"{R}/m03/stats").json()["ai_review"]["different"] == 1  # numbers only


def test_validation_and_access(hx):
    g, v, qid = _sent(hx)
    assert _review(v, qid).status_code == 422  # needs_fix needs a correction or a comment
    assert _review(v, qid, verdict="great").status_code == 422
    ai = v.get(f"{V}/questions/{qid}").json()["snapshot"]["ai_answer"]
    assert _review(v, qid, corrected_answer=ai).status_code == 422  # unchanged AI answer is not a correction
    ok = _review(v, qid, verdict="correct")
    assert ok.status_code == 200 and ok.json()["ai_review"]["verdict"] == "correct"

    other = hx.vet("v2")
    assert _review(other, qid, verdict="correct").status_code == 404  # someone else's question looks missing

    assert g.post(f"/api/v1/vet-questions/{qid}/cancel", headers=H).status_code == 200
    assert _review(v, qid, verdict="correct").status_code in (404, 409)  # cancelled: no more reviews


def test_consent_withdrawal_drops_review_text_and_the_copy(hx):
    g, v, qid = _sent(hx)
    _review(v, qid, comment="경고 누락")
    det = hx.op.get(f"{R}/m03/pending/{qid}").json()
    body = {"revision_id": None, "review_id": det["review"]["id"], "question": det["question"], "ai_answer": det["ai_answer"],
            "note_to_vet": None, "vet_reply": "경고 누락"}
    assert hx.op.post(f"{R}/m03/pending/{qid}/import", json=body, headers=H).status_code == 201
    assert g.patch(f"/api/v1/vet-questions/{qid}", json={"quality_use_consent": False}, headers=H).status_code == 200
    d = v.get(f"{V}/questions/{qid}").json()
    assert d["ai_review"]["comment"] is None and d["ai_review"]["verdict"] == "needs_fix"
    assert hx.op.get(f"{R}/m03/stats").json()["import"]["imported"] == 0


def test_new_review_after_import_flags_recheck_and_stale_import_is_refused(hx):
    g, v, qid = _sent(hx)
    _review(v, qid, comment="첫 검토")
    det = hx.op.get(f"{R}/m03/pending/{qid}").json()
    body = {"revision_id": None, "review_id": det["review"]["id"], "question": det["question"], "ai_answer": det["ai_answer"],
            "note_to_vet": None, "vet_reply": "첫 검토"}
    assert hx.op.post(f"{R}/m03/pending/{qid}/import", json=body, headers=H).status_code == 201

    _review(v, qid, comment="다시 보니 더 위험")
    assert hx.op.get(f"{R}/m03/stats").json()["import"]["needs_recheck"] == 1
    stale = hx.op.post(f"{R}/m03/pending/{qid}/import", json=body, headers=H)
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "source_changed"

    # a reply sent later joins the next import together with the latest review
    assert v.post(f"{V}/questions/{qid}/reply", json={"text": "내원해 주세요."}, headers=H).status_code == 200
    pend = hx.op.get(f"{R}/m03/pending").json()["items"]
    assert len(pend) == 1 and pend[0]["revision_id"] and pend[0]["review_id"]
    gv = g.get(f"/api/v1/vet-questions/{qid}").json()
    assert "내원해 주세요." in str(gv) and "더 위험" not in str(gv)  # the guardian gets the reply only
