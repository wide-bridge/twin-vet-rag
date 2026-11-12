"""Settings (P08: dog profile + 주치동물병원), clinic search/match and care (P09) on the throwaway test DB.
Kakao Local is replaced by a fake; the LLM is the scripted FakeLLM. No network, no cost."""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from backend.app.config import Settings
from backend.app.db.models import Clinic, Consultation, DogProfile
from backend.app.ingest.clinics import to_row, upsert
from backend.app.main import create_app
from backend.app.rag.embeddings import FakeEmbedder
from backend.app.rag.llm import FakeLLM, LLMError
from backend.app.services import clinics, profile, worker
from tests.test_api import IN_SCOPE, _knowledge

H = {"X-TwinVet-Client": "web"}
DOG = {"name": "두부", "breed": "푸들", "birth_year": 2015, "birth_month": 6, "weight_kg": 5.0}
SUMMARY = {"topic": "urinary", "topics": ["urinary", "urinary", "skin", "nope"],
           "summary": "소변검사 pH 9. 알칼리뇨 가능 원인 안내, 재검 권유.", "vet_advised": True}


class FakePlaces:
    def __init__(self):
        self.calls = []

    def search(self, query, x, y, radius):
        self.calls.append((query, x, y, radius))
        return [clinics.Place("123", "행복동물병원", "서울 마포구 망원로 12", "서울 마포구 망원동 1-1", "02-123-4567", 80,
                              "http://place.map.kakao.com/123")]


def public_item(mng="1", name="행복동물병원", road="서울특별시 마포구 망원로 12, 2층 (망원동)", status="01", tel="02-123-4567"):
    return {"BPLC_NM": name, "MNG_NO": mng, "OPN_ATMY_GRP_CD": "3130000", "ROAD_NM_ADDR": road, "LOTNO_ADDR": "",
            "TELNO": tel, "CRD_INFO_X": "193000.1", "CRD_INFO_Y": "451000.2", "SALS_STTS_CD": status,
            "DAT_UPDT_PNT": "2026-11-10 22:31:49"}


@pytest.fixture()
def env(db, test_db_url):
    _knowledge(db)
    llm = FakeLLM({"classify": IN_SCOPE,
                   "answer": {"response_type": "grounded", "answer": "두부(10세 푸들)는 재검을 권해요.", "used_sources": [1]},
                   "care_summary": SUMMARY})
    settings = Settings(database_url=test_db_url, cookie_secure=False, worker_threads=0, retrieval_min_score=-1.0,
                        questions_per_minute=100, clinic_searches_per_minute=100)
    places = FakePlaces()
    app = create_app(settings, llm=llm, embedder=FakeEmbedder(), places=places, start_worker=False)
    client = TestClient(app, base_url="http://localhost:8000")

    def drain():
        while True:
            with app.state.Session() as s:
                rid = worker.claim_next(s)
            if rid is None:
                return
            worker.process_run(app.state.Session, settings, llm, app.state.embedder, rid)

    class E:
        pass

    e = E()
    e.app, e.client, e.llm, e.places, e.drain, e.db = app, client, llm, places, drain, db
    return e


# ------------------------------------------------------------------ keys / public data rows
def test_keys_and_public_rows():
    assert clinics.road_key("서울특별시 마포구 망원로 12, 2층 (망원동)") == "서울마포구망원로12"
    assert clinics.road_key("서울 마포구 망원로 12") == "서울마포구망원로12"
    assert clinics.name_key("행복 동물병원 (망원점)") == "행복동물병원망원점"
    assert to_row(public_item(tel="010-1234-5678"))["public_phone"] is None  # mobiles are never kept
    r = to_row(public_item(status="03"))
    assert r["public_phone"] == "02-123-4567" and r["business_status"] == "closed" and r["public_id"] == "3130000-1"
    assert to_row({"BPLC_NM": "", "MNG_NO": "x"}) is None


def test_match_and_upsert_keeps_kakao_ids(env):
    db = env.db
    upsert(db, [to_row(public_item("1")), to_row(public_item("2", name="다른동물병원", road="서울특별시 마포구 망원로 99"))])
    c = clinics.match(db, "행복동물병원", "서울 마포구 망원로 12", "")
    assert c is not None and c.name == "행복동물병원"
    assert clinics.match(db, "없는동물병원", "부산 해운대구 어딘가 1", "") is None
    c.kakao_place_id = "123"
    db.commit()
    upsert(db, [to_row(public_item("1", tel=""))])  # refresh keeps the Kakao id, updates the phone
    db.expire_all()
    c = db.get(Clinic, c.id)
    assert c.kakao_place_id == "123" and c.public_phone is None


# ------------------------------------------------------------------ search
def test_clinic_search_passthrough_and_validation(env):
    r = env.client.get("/api/v1/clinics/search", params={"q": "망원"})
    assert r.status_code == 200 and r.json()["items"][0]["place_id"] == "123"
    assert env.places.calls[-1][0] == "망원 동물병원"  # area/name only gets the category added
    r = env.client.get("/api/v1/clinics/search", params={"emergency": "true", "x": 126.91, "y": 37.556})
    assert r.status_code == 200 and env.places.calls[-1][0] == "24시 동물병원" and env.places.calls[-1][3] == 10000
    assert env.client.get("/api/v1/clinics/search").status_code == 400
    assert env.client.get("/api/v1/clinics/search", params={"q": "a", "x": 1, "y": 2}).status_code == 400
    assert env.client.get("/api/v1/clinics/search", params={"q": "a" * 61}).status_code == 400


def test_clinic_search_unavailable_without_key(db, test_db_url):
    app = create_app(Settings(database_url=test_db_url, cookie_secure=False, worker_threads=0, kakao_rest_api_key=None),
                     llm=FakeLLM(), embedder=FakeEmbedder(), start_worker=False)
    app.state.places = None
    r = TestClient(app, base_url="http://localhost:8000").get("/api/v1/clinics/search", params={"q": "망원"})
    assert r.status_code == 503 and r.json()["error"]["code"] == "clinic_search_unavailable"


# ------------------------------------------------------------------ profile
def test_profile_lifecycle(env):
    c = env.client
    assert c.get("/api/v1/profile").status_code == 404
    assert c.put("/api/v1/profile", json={"dog": DOG}).status_code == 403  # CSRF header required
    r = c.put("/api/v1/profile", json={"dog": DOG}, headers=H)
    assert r.status_code == 200, r.json()
    body = r.json()
    assert body["dog"]["name"] == "두부" and body["clinic"] is None and body["expires_at"]
    assert "tvc_profile" in c.cookies

    upsert(env.db, [to_row(public_item("1"))])
    m = c.post("/api/v1/clinics/match", json={"name": "행복동물병원", "road_address": "서울 마포구 망원로 12"}, headers=H).json()
    assert m["clinic"]["name"] == "행복동물병원" and m["clinic"]["public_phone"] == "02-123-4567"
    r = c.put("/api/v1/profile", json={"dog": DOG, "clinic": {"clinic_id": m["clinic"]["id"], "kakao_place_id": "123",
                                                              "kakao_place_url": "http://place.map.kakao.com/123"},
                                       "clinic_phone": "02-123-4567"}, headers=H)
    assert r.status_code == 200
    p = r.json()
    assert p["clinic"]["kakao_place_url"] == "http://place.map.kakao.com/123" and p["clinic_phone"] == "02-123-4567"

    # dog-only update keeps the clinic; bad phone is rejected
    r = c.put("/api/v1/profile", json={"dog": {**DOG, "weight_kg": 5.4}}, headers=H)
    assert r.json()["clinic"]["name"] == "행복동물병원" and r.json()["dog"]["weight_kg"] == 5.4
    r = c.put("/api/v1/profile", json={"dog": DOG, "clinic_phone": "전화"}, headers=H)
    assert r.status_code == 422 and r.json()["error"]["fields"] == ["clinic_phone"]
    # both or neither of clinic_id / manual_name -> 422
    assert c.put("/api/v1/profile", json={"dog": DOG, "clinic": {}}, headers=H).status_code == 422

    # removing the clinic also clears the phone
    r = c.put("/api/v1/profile", json={"dog": DOG, "clinic": None}, headers=H)
    assert r.json()["clinic"] is None and r.json()["clinic_phone"] is None

    assert c.delete("/api/v1/profile", headers=H).status_code == 204
    assert c.get("/api/v1/profile").status_code == 404


def test_manual_clinic_stores_only_typed_name_and_kakao_ids(env):
    c = env.client
    r = c.put("/api/v1/profile", json={"dog": DOG, "clinic": {"manual_name": "새로연동물병원", "kakao_place_id": "987",
                                                              "kakao_place_url": "javascript:alert(1)"}}, headers=H)
    assert r.status_code == 200
    row = env.db.query(Clinic).filter_by(source="manual").one()
    assert row.name == "새로연동물병원" and row.kakao_place_id == "987" and row.kakao_place_url is None
    assert row.road_address is None and row.public_phone is None
    bad = c.put("/api/v1/profile", json={"dog": DOG, "clinic": {"manual_name": "<b>x</b>"}}, headers=H)
    assert bad.status_code == 422


def test_consultation_links_profile_and_snapshot_stays(env):
    c = env.client
    c.post("/api/v1/sessions", headers=H)
    upsert(env.db, [to_row(public_item("1"))])
    cid_clinic = env.db.query(Clinic).one().id
    c.put("/api/v1/profile", json={"dog": DOG, "clinic": {"clinic_id": str(cid_clinic)}}, headers=H)
    cid = c.post("/api/v1/consultations", json={"dog": DOG}, headers=H).json()["consultation_id"]
    con = env.db.get(Consultation, uuid.UUID(cid))
    prof = env.db.query(DogProfile).one()
    assert con.profile_id == prof.id and con.clinic_id == cid_clinic
    c.put("/api/v1/profile", json={"dog": {**DOG, "breed": "말티즈"}}, headers=H)
    assert c.get(f"/api/v1/consultations/{cid}/messages").json()["dog"]["breed"] == "푸들"
    # deleting the profile leaves the consultation (link set to null)
    c.delete("/api/v1/profile", headers=H)
    env.db.expire_all()
    assert env.db.get(Consultation, uuid.UUID(cid)).profile_id is None


def test_profile_expiry_and_purge(env):
    c = env.client
    c.put("/api/v1/profile", json={"dog": DOG}, headers=H)
    row = env.db.query(DogProfile).one()
    from datetime import datetime, timedelta, timezone
    row.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    env.db.commit()
    assert c.get("/api/v1/profile").status_code == 404
    assert profile.purge_expired_profiles(env.db) == 1


def test_estimated_age_grows_from_save_time(env):
    c = env.client
    c.put("/api/v1/profile", json={"dog": {"breed": "믹스견", "age_months": 30}}, headers=H)
    row = env.db.query(DogProfile).one()
    from datetime import datetime, timedelta, timezone
    row.updated_at = datetime.now(timezone.utc) - timedelta(days=95)
    env.db.commit()
    assert c.get("/api/v1/profile").json()["dog"]["age_months"] in (33, 34)


# ------------------------------------------------------------------ care
def _consult(c, q="소변검사 pH가 9예요"):
    cid = c.post("/api/v1/consultations", json={"dog": DOG}, headers=H).json()["consultation_id"]
    c.post(f"/api/v1/consultations/{cid}/questions", json={"text": q, "client_request_id": uuid.uuid4().hex}, headers=H)
    return cid


def test_care_cards_and_summary(env):
    c = env.client
    c.post("/api/v1/sessions", headers=H)
    cid = _consult(c)
    items = c.get("/api/v1/care/consultations").json()["items"]
    assert len(items) == 1 and items[0]["tags"] == ["in_progress"] and not items[0]["summary_fresh"]
    env.drain()
    item = c.get("/api/v1/care/consultations").json()["items"][0]
    assert item["first_question"] == "소변검사 pH가 9예요" and item["summary"] is None and item["dog"]["name"] == "두부"

    r = c.post(f"/api/v1/consultations/{cid}/summary", headers=H)
    assert r.status_code == 200
    card = r.json()
    assert card["topic"] == "urinary" and card["topics"] == ["urinary", "skin"] and card["vet_advised"] is True
    assert card["summary_fresh"] and card["tags"] == ["vet_advised"]
    n = len(env.llm.calls)
    c.post(f"/api/v1/consultations/{cid}/summary", headers=H)  # fresh -> no new model call
    assert len(env.llm.calls) == n
    assert "<상담 기록>" in env.llm.calls[-1]["user"] and "[보호자] 소변검사 pH가 9예요" in env.llm.calls[-1]["user"]

    # a follow-up makes the summary stale
    c.post(f"/api/v1/consultations/{cid}/questions", json={"text": "재검은 언제?", "client_request_id": uuid.uuid4().hex}, headers=H)
    env.drain()
    assert not c.get("/api/v1/care/consultations").json()["items"][0]["summary_fresh"]


def test_care_is_per_session_and_summary_failure(env):
    c = env.client
    c.post("/api/v1/sessions", headers=H)
    cid = _consult(c)
    env.drain()
    other = TestClient(env.app, base_url="http://localhost:8000")
    other.post("/api/v1/sessions", headers=H)
    assert other.get("/api/v1/care/consultations").json()["items"] == []
    assert other.post(f"/api/v1/consultations/{cid}/summary", headers=H).status_code == 404
    env.llm.responses["care_summary"] = LLMError("boom")
    r = c.post(f"/api/v1/consultations/{cid}/summary", headers=H)
    assert r.status_code == 503 and r.json()["error"]["code"] == "summary_unavailable"


def test_clip_summary_keeps_short_and_cuts_long():
    from backend.app.services.care import clip_summary
    short = "3일째 구토함. 금식 후 소량 급여 안내."
    assert clip_summary(short) == short
    three = "가" * 40 + ". " + "나" * 40 + ". " + "다" * 40 + "."
    assert clip_summary(three) == "가" * 40 + ". " + "나" * 40 + "."
    two_long = "가" * 70 + ". " + "나" * 70 + "."
    assert clip_summary(two_long) == "가" * 70 + "."
    one_long = " ".join(["단어"] * 80)
    out = clip_summary(one_long)
    assert len(out) <= 120 and out.endswith("…")
