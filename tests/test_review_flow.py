"""MVP1-A completion check (docs/01 §4, docs/04 §7) on the throwaway test DB with mock data and FakeEmbedder:
bundle -> label -> independent re-verification -> candidate -> approval -> regression gate -> test activation
-> searchable; plus the guards (auth, hidden labels, operator labels, production refusals). No network, no cost."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from backend.app.config import Settings
from backend.app.db.models import Document
from backend.app.db.models_review import KnowledgeCandidate, ReviewEvent, ReviewItem
from backend.app.main import create_app
from backend.app.rag.embeddings import FakeEmbedder
from backend.app.rag.llm import FakeLLM
from backend.app.rag.retrieval import search
from backend.app.services import review as svc

H = {"X-TwinVet-Client": "web"}
B = "/api/v1/review"

GOOD = {"accuracy": "appropriate", "evidence_support": "supported", "sufficiency": "answerable",
        "guidance": "appropriate", "reuse_scope": "general_candidate", "revision_evidence": ["1"],
        "concepts": [{"code": "symptom:coughing", "assertion": "owner_reported"},
                     {"code": "condition:tracheal_collapse", "assertion": "vet_assessed"}]}


@pytest.fixture()
def hitl(db, test_db_url):
    settings = Settings(database_url=test_db_url, cookie_secure=False, worker_threads=0, app_env="test")
    app = create_app(settings, llm=FakeLLM({}), embedder=FakeEmbedder(), start_worker=False)
    with app.state.Session() as s:
        svc.seed_ontology(s)
        svc.create_mock_items(s)
        keys = {name: svc.create_reviewer(s, role, name)[1]
                for name, role in (("vet1", "vet"), ("vet2", "vet"), ("vet3", "vet"), ("op", "operator"))}

    def client(name: str | None) -> TestClient:
        c = TestClient(app, base_url="http://localhost:8000")
        if name:
            r = c.post(f"{B}/login", json={"access_key": keys[name]}, headers=H)
            assert r.status_code == 200, r.text
        return c

    class E:
        pass

    e = E()
    e.app, e.settings, e.client, e.keys = app, settings, client, keys
    return e


def _item_id(c: TestClient, ref_question_part: str) -> str:
    items = c.get(f"{B}/items").json()["items"]
    return next(i["id"] for i in items if ref_question_part in i["question"])


def test_auth_required_and_bad_key(hitl):
    anon = hitl.client(None)
    assert anon.get(f"{B}/items").status_code == 401
    assert anon.post(f"{B}/login", json={"access_key": "tvr_wrong_key_123"}, headers=H).status_code == 401
    assert anon.post(f"{B}/login", json={"access_key": hitl.keys["vet1"]}).status_code == 403  # CSRF header
    me = hitl.client("vet1").get(f"{B}/me").json()
    assert me["role"] == "vet" and me["production"] is False


def test_full_stage_a_flow(hitl):
    v1, v2, v3, op = (hitl.client(n) for n in ("vet1", "vet2", "vet3", "op"))
    iid = _item_id(v1, "거위 소리")

    # mock bundle with an AI suggestion shown; nothing from other labelers yet
    d = v1.get(f"{B}/items/{iid}").json()
    assert d["item"]["is_mock"] and d["item"]["question"].startswith("[모의]")
    assert d["ai_suggestion"]["author_kind"] == "ai" and d["other_labels"] == [] and d["item"]["review_status"] == "draft"

    # automatic check: incomplete label -> 422 with field reasons
    r = v1.post(f"{B}/items/{iid}/label/submit", json={"accuracy": "minor_edit"}, headers=H)
    assert r.status_code == 422 and "revised_answer" in r.json()["error"]["fields"]

    # operator test label never changes the review status
    assert op.post(f"{B}/items/{iid}/label/submit", json=GOOD, headers=H).status_code == 200
    assert v1.get(f"{B}/items/{iid}").json()["item"]["review_status"] == "draft"

    # vet1: draft save (time recorded), then submit -> vet_reviewed
    assert v1.put(f"{B}/items/{iid}/label", json={**GOOD, "active_seconds": 95}, headers=H).status_code == 200
    d = v1.post(f"{B}/items/{iid}/label/submit", json={**GOOD, "active_seconds": 30}, headers=H).json()
    assert d["item"]["review_status"] == "vet_reviewed" and d["my_label"]["active_seconds"] == 125
    assert d["my_label"]["ai_suggestion_shown"] is True
    assert {c["code"] for c in d["my_label"]["concepts"]} == {"symptom:coughing", "condition:tracheal_collapse"}
    # a submitted label is final
    assert v1.put(f"{B}/items/{iid}/label", json=GOOD, headers=H).status_code == 409

    # vet2 cannot see vet1's label before submitting (independent labeling)
    d2 = v2.get(f"{B}/items/{iid}").json()
    assert d2["other_labels"] == [] and d2["other_label_count"] == 2
    d2 = v2.post(f"{B}/items/{iid}/label/submit", json=GOOD, headers=H).json()
    assert d2["item"]["review_status"] == "independently_verified"
    assert len(d2["other_labels"]) == 2  # vet1 + operator, visible after own submit

    # candidate: vet1 writes it and cannot approve it; vet2 (second labeler, not an author) approves
    cand = v1.post(f"{B}/candidates", json={"item_id": iid, "kind": "general", "title": "거위 소리 기침 안내",
                                            "content": "흥분할 때 심해지는 거위 소리 기침은 소형견에서 기관 허탈과 관련될 수 있다. 호흡이 가쁘면 즉시 내원.",
                                            "conditions": {"breed_size": "소형견"}, "evidence": ["[모의] 호흡기 교재"]}, headers=H)
    assert cand.status_code == 201, cand.text
    cand = cand.json()
    assert cand["is_mock"] and cand["title"].startswith("[모의]") and cand["publish_status"] == "unapproved"
    r = v1.post(f"{B}/candidates/{cand['id']}/approve", headers=H)
    assert r.status_code == 409 and "다른 수의사" in r.json()["error"]["message"]
    assert v2.post(f"{B}/candidates/{cand['id']}/approve", headers=H).json()["publish_status"] == "approved"

    # editing after approval clears it; re-approve
    body = {"item_id": iid, "kind": "general", "title": "거위 소리 기침 안내", "evidence": ["[모의] 호흡기 교재"],
            "conditions": {"breed_size": "소형견"},
            "content": "흥분할 때 심해지는 거위 소리 기침은 소형견에서 기관 허탈과 관련될 수 있다. 가슴줄을 쓰고 호흡이 가쁘면 즉시 내원."}
    assert v3.put(f"{B}/candidates/{cand['id']}", json=body, headers=H).json()["publish_status"] == "unapproved"
    # vet3 edited it, so vet3 can no longer approve; vet2 re-approves
    assert v3.post(f"{B}/candidates/{cand['id']}/approve", headers=H).status_code == 409
    assert v2.post(f"{B}/candidates/{cand['id']}/approve", headers=H).json()["publish_status"] == "approved"

    emb = FakeEmbedder()
    with hitl.app.state.Session() as s:
        c = s.get(KnowledgeCandidate, __import__("uuid").UUID(cand["id"]))
        # no regression evaluation yet -> refused
        with pytest.raises(svc.ReviewError, match="검색 영향 점검"):
            svc.activate_candidate(s, c, emb, production=False)
        res = svc.evaluate_candidate(s, c, emb, ["강아지가 토해요", "귀를 긁어요"], top_k=6, max_spillover=0.5)
        assert res["passed"] and res["kind"] == "search_impact(mock_flow)"
        # mock knowledge is never activated in production
        with pytest.raises(svc.ReviewError, match="모의"):
            svc.activate_candidate(s, c, emb, production=True)
        out = svc.activate_candidate(s, c, emb, production=False)
        assert out["activated"] == 1
        doc = s.execute(select(Document).where(Document.dataset_code == "vetreview")).scalar_one()
        assert doc.document_type == "vet_knowledge" and doc.meta["is_mock"] is True
        # application conditions are inside the searchable text, so the answer model sees them
        hit = search(s, emb.embed([svc._doc_text(c)])[0], model=emb.model, life_stage=None, allow_mock=True)
        assert any("적용 조건: 견 크기 소형견" in h.content for h in hit if h.document_id == doc.id)
        # searchable in the test environment, hidden by the production filter
        qv = emb.embed([svc._doc_text(c)])[0]
        assert any(h.document_id == doc.id for h in search(s, qv, model=emb.model, life_stage=None, allow_mock=True))
        assert not any(h.document_id == doc.id for h in search(s, qv, model=emb.model, life_stage=None, allow_mock=False))
        events = [e.event for e in s.execute(select(ReviewEvent).order_by(ReviewEvent.created_at)).scalars()]
        for ev in ("label_submitted", "status_changed", "candidate_approved", "approval_cleared", "candidate_evaluated",
                   "candidate_activated"):
            assert ev in events
    assert v1.get(f"{B}/candidates").json()["items"][0]["publish_status"] == "active"


def test_disputed_and_adjudication(hitl):
    v1, v2 = hitl.client("vet1"), hitl.client("vet2")
    iid = _item_id(v1, "소변")
    bad = {"accuracy": "inappropriate", "evidence_support": "partial", "sufficiency": "needs_more_info",
           "guidance": "inappropriate", "reuse_scope": "excluded", "safety_issue": True,
           "revised_answer": "바로 동물병원에 연락하세요.", "revision_reason": "배뇨 불가는 응급일 수 있음"}
    v1.post(f"{B}/items/{iid}/label/submit", json=bad, headers=H)
    assert v1.post(f"{B}/items/{iid}/adjudicate", json={"dispute_type": "info_lacking"}, headers=H).status_code == 409
    d = v2.post(f"{B}/items/{iid}/label/submit", json={**bad, "guidance": "missing_important"}, headers=H).json()
    assert d["item"]["review_status"] == "disputed"
    assert v2.post(f"{B}/items/{iid}/adjudicate", json={"dispute_type": "nope"}, headers=H).status_code == 422
    d = v2.post(f"{B}/items/{iid}/adjudicate", json={"dispute_type": "info_lacking", "note": "배뇨량 정보 필요"}, headers=H).json()
    assert d["item"]["dispute_type"] == "info_lacking"


def test_hidden_ai_suggestion_and_operator_approval_rules(hitl):
    v1, op = hitl.client("vet1"), hitl.client("op")
    iid = _item_id(v1, "귀를")
    d = v1.get(f"{B}/items/{iid}").json()
    assert d["item"]["show_ai_suggestion"] is False and d["ai_suggestion"] is None
    d = v1.post(f"{B}/items/{iid}/label/submit", json={**GOOD, "reuse_scope": "case_only", "concepts": []}, headers=H).json()
    assert d["my_label"]["ai_suggestion_shown"] is False and d["ai_suggestion"] is not None  # shown after submit

    # operator may approve mock candidates in a test environment only
    cand = op.post(f"{B}/candidates", json={"item_id": iid, "kind": "case", "title": "귀 사례",
                                            "content": "귀를 긁고 냄새가 나면 귀 검사를 권한다.", "evidence": ["1"]}, headers=H).json()
    assert op.post(f"{B}/candidates/{cand['id']}/approve", headers=H).json()["publish_status"] == "approved"
    with hitl.app.state.Session() as s:
        c2 = svc.create_candidate(s, s.get(svc.Reviewer, __import__("uuid").UUID(op.get(f"{B}/me").json()["id"])),
                                  {"kind": "general", "title": "실데이터 후보", "content": "모의가 아닌 후보의 내용입니다.",
                                   "evidence": ["x"]})
        assert not c2.is_mock
    assert op.post(f"{B}/candidates/{c2.id}/approve", headers=H).status_code == 409  # not mock


def test_production_refuses_operator_approval(db, test_db_url):
    settings = Settings(database_url=test_db_url, cookie_secure=False, worker_threads=0, app_env="production")
    assert settings.hitl_production
    app = create_app(settings, llm=FakeLLM({}), embedder=FakeEmbedder(), start_worker=False)
    with app.state.Session() as s:
        svc.seed_ontology(s)
        svc.create_mock_items(s)
        _, key = svc.create_reviewer(s, "operator", "op")
        item = s.execute(select(ReviewItem).where(ReviewItem.source_ref == "mock-004")).scalar_one()
    c = TestClient(app, base_url="http://localhost:8000")
    c.post(f"{B}/login", json={"access_key": key}, headers=H)
    cand = c.post(f"{B}/candidates", json={"item_id": str(item.id), "kind": "case", "title": "t1",
                                           "content": "운영 환경 승인 거부 확인용 내용", "evidence": ["1"]}, headers=H).json()
    r = c.post(f"{B}/candidates/{cand['id']}/approve", headers=H)
    assert r.status_code == 409 and "운영 환경" in r.json()["error"]["message"]


def test_seed_is_idempotent_and_split_table(db, tmp_path):
    a = svc.seed_ontology(db)
    b = svc.seed_ontology(db)
    assert a["concepts_added"] > 50 and b["concepts_added"] == 0 and b["relations_added"] == 0

    import json

    from backend.app.ingest.split import build_split_table, write_split_table

    recs = [{"source_record_id": f"r{i}", "split": "train", "department": "내과" if i % 3 else "안과",
             "question": f"질문 {i % 7}"} for i in range(40)]  # duplicates: same question text
    t1, t2 = build_split_table(recs, 5, 1), build_split_table(recs, 5, 1)
    assert t1 == t2
    fold_by_group: dict = {}
    for r in t1:
        assert fold_by_group.setdefault(r["dup_group"], r["fold"]) == r["fold"]  # a group never spans folds
    (tmp_path / "qa_cases.jsonl").write_text("\n".join(json.dumps(
        {"source_record_id": r["source_record_id"], "department": r["department"],
         "content": f"보호자 질문: {r['question']}\n수의사 답변: 답"}, ensure_ascii=False) for r in recs), encoding="utf-8")
    rep = write_split_table(tmp_path, 5, 1)
    assert rep["rows"] == 40 and rep["dup_groups"] == 7


# ---------------------------------------------------------------- dataset bundles + AI pre-labels (no network)
def test_dataset_bundles_with_prelabels(db, test_db_url):
    from backend.app.ingest import load
    from backend.app.ingest.load import DocInput
    from backend.app.services import review_dataset as ds

    E = FakeEmbedder()
    run = load.start_run(db, "synthetic", "v1", E.model)
    load.stage(db, run, [DocInput("q1", "qa_case", "내과 상담 사례", "내과", [], ["adult"], "h-q1", "s/q1.json", {},
                                  [{"ordinal": 0, "content": "보호자 질문: 구토\n수의사 답변: 탈수를 확인하세요", "content_hash": "c",
                                    "tokens": 10, "locator": None}])])
    load.embed_pending(db, run, E)
    load.activate(db, run)
    svc.seed_ontology(db)

    prelabel = {"accuracy": "minor_edit", "evidence_support": "supported", "sufficiency": "answerable",
                "guidance": "missing_important", "safety_issue": False, "revised_answer": "탈수가 보이면 내원하세요.",
                "revision_reason": "내원 기준 누락", "revision_evidence": ["1", "9", "x"], "reuse_scope": "conditional",
                "concepts": [{"code": "symptom:vomiting", "assertion": "owner_reported"},
                             {"code": "symptom:vomiting", "assertion": "owner_reported"},
                             {"code": "condition:nope", "assertion": "ai_inferred"},
                             {"code": "action:vet_visit_soon", "assertion": "vet_assessed"}]}
    llm = FakeLLM({"classify": {"scope": "in_scope", "search_query": "구토", "question_age_text": "",
                                "question_unclear": False, "clarification_question": ""},
                   "answer": {"response_type": "grounded", "answer": "탈수를 확인하세요.", "used_sources": [1]},
                   "prelabel": prelabel})
    settings = Settings(database_url=test_db_url, retrieval_min_score=-1.0, app_env="test")
    rows = [{"eval_id": f"aihub59-dev-{i}", "subset": "dev", "department": "내과", "life_stages": ["adult"],
             "question": f"3살 강아지가 구토를 해요 {i}"} for i in range(12)]
    rep = ds.build_dataset_items(db, rows, settings=settings, llm=llm, embedder=E, n=10, hide_pct=30)
    assert rep.created == 10 and rep.prelabeled == 10 and rep.failed == 0
    assert 0 < rep.hidden < 10  # stable hash share
    items = db.execute(select(ReviewItem).where(ReviewItem.origin == "dataset")).scalars().all()
    it = items[0]
    assert not it.is_mock and it.review_status == "draft" and it.citations[0]["excerpt"].startswith("보호자 질문")
    assert it.versions["prompt_version"] and it.dog["breed"] == "모름" and it.dog["age_months"] == 36
    # the cleaned AI label: bad evidence numbers, duplicate/unknown/vet_assessed concepts dropped
    from backend.app.db.models_review import ReviewLabel, LabelConcept
    lab = db.execute(select(ReviewLabel).where(ReviewLabel.item_id == it.id)).scalar_one()
    assert lab.author_kind == "ai" and lab.revision_evidence == ["1"]
    assert db.query(LabelConcept).filter(LabelConcept.label_id == lab.id).count() == 1

    # idempotent: a second run skips existing rows without model calls
    calls = len(llm.calls)
    rep2 = ds.build_dataset_items(db, rows, settings=settings, llm=llm, embedder=E, n=10)
    assert rep2.created == 2 and rep2.skipped_existing == 10 and len(llm.calls) == calls + 2 * 3

    # an invalid pre-label (appropriate + safety) is rejected, the bundle is still created
    bad, errs = ds.clean_prelabel({**prelabel, "accuracy": "appropriate", "safety_issue": True}, 1,
                                  {k: c.status for k, c in svc.concept_index(db).items()})
    assert bad is None and "safety_issue" in errs


def test_real_candidate_search_impact_check_must_be_valid(hitl):
    """A real (non-mock) candidate: too few or non-comparable questions make the check invalid, never passed."""
    emb = FakeEmbedder()
    with hitl.app.state.Session() as s:
        vet = s.execute(select(svc.Reviewer).where(svc.Reviewer.display_name == "vet1")).scalar_one()
        c = svc.create_candidate(s, vet, {"kind": "general", "title": "실제 후보", "content": "실제 후보의 내용입니다. 충분히 길게.",
                                          "conditions": {"life_stages": ["senior"]}, "evidence": ["x"]})
        assert not c.is_mock and "생애 단계 노령견" in svc.knowledge_body(c)
        r = svc.evaluate_candidate(s, c, emb, ["강아지가 토해요"] * 3, top_k=6, max_spillover=0.5, min_questions=30)
        assert r["valid"] is False and r["passed"] is False and "최소 30" in r["invalid_reason"]
        r = svc.evaluate_candidate(s, c, emb, ["강아지가 토해요"] * 30, top_k=6, max_spillover=0.5, min_questions=30)
        assert r["valid"] is False and r["comparable"] == 0  # empty index: nothing to compare
        # even a forged passed flag on a mock-flow result does not count for real knowledge
        c.publish_status, c.approved_hash = "approved", c.content_hash
        c.eval_passed, c.eval_result = True, {**r, "valid": True, "kind": "search_impact(mock_flow)"}
        s.commit()
        with pytest.raises(svc.ReviewError, match="검색 영향 점검"):
            svc.activate_candidate(s, c, emb, production=False)
