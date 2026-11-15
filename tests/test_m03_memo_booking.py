"""Vet's own dog memo (personal info, docs/06 §20) and booking info (예약 안내) on the vet screen.

The memo stays with that vet: never the guardian, the HITL import queue, operator views or another vet; it is kept per
vet and dog across questions and deleted with the dog profile. Booking info is the vet's own text."""
from __future__ import annotations

from tests.test_m03 import H, V, _connect, m3  # noqa: F401  (fixture)
from tests.test_m03_hitl import R, hx  # noqa: F401  (fixture)


def _sent(e, consent=True, vet="v1", g=None):
    if g is None:
        g, _, answer = e.guardian()
    else:
        answer = None
    v = e.vet(vet)
    lid = _connect(e, g, v, vet)
    q = g.post("/api/v1/vet-questions", headers=H, json={
        "link_id": lid, "answer_message_id": answer, "note_to_vet": None,
        "shared_fields": ["breed"], "quality_use_consent": consent}).json()
    return g, v, q["id"]


def test_memo_stays_with_the_vet_only(hx):
    g, v, qid = _sent(hx)
    r = v.put(f"{V}/questions/{qid}/memo", json={"text": "두부는 평소 건강하고 활동량이 많음"}, headers=H)
    assert r.status_code == 200, r.text
    assert r.json()["memo"]["body"] == "두부는 평소 건강하고 활동량이 많음"
    assert v.get(f"{V}/questions/{qid}").json()["memo"]["body"].startswith("두부는")

    # nowhere else: guardian, HITL queue (even with a review + consent), operator stats, another vet
    assert "활동량" not in str(g.get(f"/api/v1/vet-questions/{qid}").json())
    v.put(f"{V}/questions/{qid}/ai-review", json={"verdict": "correct"}, headers=H)
    assert "활동량" not in str(hx.op.get(f"{R}/m03/pending/{qid}").json())
    assert "활동량" not in str(hx.op.get(f"{R}/m03/stats").json())
    assert hx.vet("v2").put(f"{V}/questions/{qid}/memo", json={"text": "x"}, headers=H).status_code == 404

    # empty text deletes it; too long is refused
    assert v.put(f"{V}/questions/{qid}/memo", json={"text": "  "}, headers=H).json()["memo"] is None
    assert v.put(f"{V}/questions/{qid}/memo", json={"text": "가" * 2001}, headers=H).status_code == 422


def test_memo_is_per_dog_and_follows_the_next_question(hx):
    g, cid, answer = hx.guardian()
    v = hx.vet("v1")
    lid = _connect(hx, g, v)

    def send(ans):
        return g.post("/api/v1/vet-questions", headers=H, json={"link_id": lid, "answer_message_id": ans, "note_to_vet": None,
                                                               "shared_fields": [], "quality_use_consent": False}).json()["id"]
    q1 = send(answer)
    v.put(f"{V}/questions/{q1}/memo", json={"text": "노령, 심장 질환 이력"}, headers=H)
    q2 = send(hx.ask(g, cid, "산책은 해도 되나요?"))
    assert v.get(f"{V}/questions/{q2}").json()["memo"]["body"] == "노령, 심장 질환 이력"  # same dog, next question

    # another dog of another guardian has no memo
    _, _, q3 = _sent(hx)
    assert v.get(f"{V}/questions/{q3}").json()["memo"] is None


def test_booking_info_is_saved_by_the_vet(hx):
    v = hx.vet("v1")
    assert v.get(f"{V}/booking").json() == {"phone": None, "hours": None, "note": None}
    r = v.put(f"{V}/booking", json={"phone": "02-123-4567", "hours": "평일 9-19시, 토 9-13시", "note": "예약은 전화로 부탁드려요"}, headers=H)
    assert r.status_code == 200 and r.json()["phone"] == "02-123-4567"
    assert v.get(f"{V}/booking").json()["hours"] == "평일 9-19시, 토 9-13시"
    assert hx.vet("v2").get(f"{V}/booking").json()["phone"] is None  # each vet has their own
    assert v.put(f"{V}/booking", json={"phone": "1" * 41}, headers=H).status_code == 422
