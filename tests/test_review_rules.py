"""MVP1 HITL rules (docs/03 §4, docs/04 §7): automatic label checks, review status, publish gates, DB constraints."""
import uuid

import pytest

from backend.app.domain.review import (
    ConceptTag,
    LabelInput,
    SubmittedLabel,
    activation_block_reason,
    approval_block_reason,
    check_label,
    review_status,
)

CONCEPTS = {"symptom:vomiting": "draft", "body_part:stomach": "approved", "symptom:old": "retired"}


def ok_label(**kw) -> LabelInput:
    base = dict(accuracy="appropriate", evidence_support="supported", sufficiency="answerable",
                guidance="appropriate", reuse_scope="case_only")
    base.update(kw)
    return LabelInput(**base)


def test_complete_label_passes():
    lab = ok_label(concepts=[ConceptTag("symptom:vomiting", "owner_reported"),
                             ConceptTag("symptom:vomiting", "vet_assessed")])  # same concept, two sources
    assert check_label(lab, CONCEPTS) == {}


def test_required_and_allowed_values():
    errs = check_label(LabelInput(accuracy="great"), CONCEPTS)
    assert errs["accuracy"] == "허용되지 않는 값입니다"
    for f in ("evidence_support", "sufficiency", "guidance", "reuse_scope"):
        assert errs[f] == "필수 항목입니다"


def test_edit_needs_revision_and_reason():
    errs = check_label(ok_label(accuracy="minor_edit"), CONCEPTS)
    assert set(errs) >= {"revised_answer", "revision_reason"}
    assert check_label(ok_label(accuracy="inappropriate", guidance="inappropriate", revised_answer="수정안",
                                revision_reason="근거와 다름"), CONCEPTS) == {}


def test_reuse_candidate_needs_evidence_and_determinable():
    assert "revision_evidence" in check_label(ok_label(reuse_scope="general_candidate"), CONCEPTS)
    errs = check_label(ok_label(accuracy="undeterminable", reuse_scope="conditional", revision_evidence=["c1"]), CONCEPTS)
    assert "reuse_scope" in errs
    assert check_label(ok_label(reuse_scope="conditional", revision_evidence=["c1"]), CONCEPTS) == {}


def test_contradictions_and_concepts():
    assert "guidance" in check_label(ok_label(guidance="inappropriate"), CONCEPTS)
    assert "safety_issue" in check_label(ok_label(safety_issue=True), CONCEPTS)
    assert "concepts" in check_label(ok_label(concepts=[ConceptTag("symptom:nope", "owner_reported")]), CONCEPTS)
    assert "concepts" in check_label(ok_label(concepts=[ConceptTag("symptom:old", "owner_reported")]), CONCEPTS)
    assert "concepts" in check_label(ok_label(concepts=[ConceptTag("symptom:vomiting", "guess")]), CONCEPTS)
    dup = [ConceptTag("body_part:stomach", "vet_assessed")] * 2
    assert "concepts" in check_label(ok_label(concepts=dup), CONCEPTS)


def sub(kind, rid, acc="appropriate", gui="appropriate", reuse="case_only", ev="supported", suf="answerable",
        safety=False):
    return SubmittedLabel(kind, rid, acc, gui, reuse, ev, suf, safety)


def test_review_status_counts_only_vets():
    assert review_status([]) == "draft"
    assert review_status([sub("operator", "op"), sub("ai", None)]) == "draft"
    assert review_status([sub("vet", "v1"), sub("operator", "op", acc="inappropriate")]) == "vet_reviewed"
    assert review_status([sub("vet", "v1"), sub("vet", "v2")]) == "independently_verified"
    assert review_status([sub("vet", "v1"), sub("vet", "v2", reuse="excluded")]) == "disputed"
    # safety, evidence and sufficiency differences are disputes too (Codex review 2026-11-14)
    both_edit = dict(acc="minor_edit", gui="missing_important")
    assert review_status([sub("vet", "v1", **both_edit, safety=True), sub("vet", "v2", **both_edit)]) == "disputed"
    assert review_status([sub("vet", "v1", ev="partial"), sub("vet", "v2")]) == "disputed"
    assert review_status([sub("vet", "v1", suf="needs_more_info"), sub("vet", "v2")]) == "disputed"
    # the same vet twice is still one reviewer (self-check is not independent)
    assert review_status([sub("vet", "v1"), sub("vet", "v1")]) == "vet_reviewed"


def approve(**kw):
    base = dict(approver_role="vet", approver_id="v2", production=True, is_mock=False,
                item_status="independently_verified", reuse_scope_ok=True, candidate_authors={"v1"})
    base.update(kw)
    return approval_block_reason(**base)


def test_approval_rules():
    assert approve() is None                                # the second labeler may approve (two-vet model)
    assert approve(approver_id="v1") is not None            # wrote the candidate
    assert approve(candidate_authors={"v1", "v2"}) is not None  # edited the candidate
    assert approve(item_status="vet_reviewed") is not None  # not independently verified
    assert approve(reuse_scope_ok=False) is not None
    assert approve(approver_role="operator") is not None    # production
    assert approve(approver_role="operator", production=False) is not None  # real data
    assert approve(approver_role="operator", production=False, is_mock=True, item_status="draft") is None


def activate(**kw):
    base = dict(publish_status="approved", approved_hash="h", content_hash="h", eval_passed=True,
                is_mock=False, production=True)
    base.update(kw)
    return activation_block_reason(**base)


def test_activation_rules():
    assert activate() is None
    assert activate(content_hash="h2") is not None   # edited after approval
    assert activate(eval_passed=None) is not None
    assert activate(eval_passed=False) is not None
    assert activate(publish_status="unapproved") is not None
    assert activate(is_mock=True) is not None         # mock never reaches production
    assert activate(is_mock=True, production=False) is None


# ---------------------------------------------------------------- DB constraints (local test DB only)
def _item(db, origin="mock", ref=None):
    from backend.app.db.models_review import ReviewItem

    it = ReviewItem(origin=origin, is_mock=origin == "mock", source_ref=ref or str(uuid.uuid4()),
                    question="q", ai_answer="a", content_hash="0" * 64)
    db.add(it)
    db.flush()
    return it


def _reviewer(db, role="vet"):
    from backend.app.db.models_review import Reviewer

    r = Reviewer(role=role, display_name="t", access_key_hash=uuid.uuid4().hex * 2)
    db.add(r)
    db.flush()
    return r


def test_db_mock_flag_must_match_origin(db):
    from sqlalchemy.exc import IntegrityError
    from backend.app.db.models_review import ReviewItem

    db.add(ReviewItem(origin="dataset", is_mock=True, source_ref="x", question="q", ai_answer="a", content_hash="0" * 64))
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()


def test_db_one_label_per_reviewer_and_one_ai(db):
    from sqlalchemy.exc import IntegrityError
    from backend.app.db.models_review import ReviewLabel

    it, vet = _item(db), _reviewer(db)
    db.add(ReviewLabel(item_id=it.id, author_kind="vet", reviewer_id=vet.id))
    db.add(ReviewLabel(item_id=it.id, author_kind="ai"))
    db.flush()
    db.add(ReviewLabel(item_id=it.id, author_kind="vet", reviewer_id=vet.id))
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()
    it = _item(db)
    db.add(ReviewLabel(item_id=it.id, author_kind="ai"))
    db.add(ReviewLabel(item_id=it.id, author_kind="ai"))
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()


def test_db_ai_label_has_no_reviewer_and_approved_needs_hash(db):
    from sqlalchemy.exc import IntegrityError
    from backend.app.db.models_review import KnowledgeCandidate, ReviewLabel

    it, vet = _item(db), _reviewer(db)
    db.add(ReviewLabel(item_id=it.id, author_kind="ai", reviewer_id=vet.id))
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()
    db.add(KnowledgeCandidate(kind="case", title="t", content="c", content_hash="h", is_mock=True,
                              publish_status="approved"))
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()


def test_db_vet_knowledge_document_type_allowed(db):
    from backend.app.db.models import Document

    db.add(Document(dataset_code="vetreview", source_record_id="k1", document_type="vet_knowledge", title="t",
                    source_name="TwinVet 수의사 검토"))
    db.flush()
    db.rollback()
