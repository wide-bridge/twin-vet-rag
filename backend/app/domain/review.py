"""MVP1 HITL rules (docs/03 §4, docs/04 §7): label values, automatic checks, review status, publish gates.

Pure functions only (no DB), so the rules can be tested without PostgreSQL and reused by the API and scripts.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

AUTHOR_KINDS = ("vet", "operator", "ai")
ACCURACY = ("appropriate", "minor_edit", "inappropriate", "undeterminable")
EVIDENCE_SUPPORT = ("supported", "partial", "unsupported")
SUFFICIENCY = ("answerable", "needs_more_info")
GUIDANCE = ("appropriate", "missing_important", "inappropriate")
REUSE_SCOPE = ("case_only", "conditional", "general_candidate", "excluded")
ASSERTIONS = ("owner_reported", "ai_inferred", "vet_assessed", "test_confirmed")

REVIEW_STATUSES = ("draft", "vet_reviewed", "independently_verified", "disputed")
DISPUTE_TYPES = ("info_lacking", "evidence_conflict", "acceptable_difference")
PUBLISH_STATUSES = ("unapproved", "approved", "active", "rejected", "retired")
CONCEPT_SCHEMES = ("topic", "body_part", "symptom", "condition", "test", "observation", "action")
CONCEPT_STATUSES = ("draft", "approved", "retired")
PREDICATES = ("is_a", "part_of", "located_in", "associated_with", "red_flag_for", "recommends")

# fields compared when deciding whether two independent vet labels agree
AGREEMENT_FIELDS = ("accuracy", "guidance", "reuse_scope")

REQUIRED_FIELDS = {
    "accuracy": ACCURACY,
    "evidence_support": EVIDENCE_SUPPORT,
    "sufficiency": SUFFICIENCY,
    "guidance": GUIDANCE,
    "reuse_scope": REUSE_SCOPE,
}

MAX_TEXT = {"revised_answer": 4000, "revision_reason": 1000, "note": 1000}


@dataclass
class ConceptTag:
    code: str  # "<scheme>:<code>", e.g. "symptom:vomiting"
    assertion: str


@dataclass
class LabelInput:
    accuracy: str | None = None
    evidence_support: str | None = None
    sufficiency: str | None = None
    guidance: str | None = None
    safety_issue: bool = False
    revised_answer: str | None = None
    revision_reason: str | None = None
    revision_evidence: list[str] = field(default_factory=list)  # citation ids / source notes
    reuse_scope: str | None = None
    note: str | None = None
    concepts: list[ConceptTag] = field(default_factory=list)


def check_label(label: LabelInput, concept_status: dict[str, str]) -> dict[str, str]:
    """Automatic checks run on submit. Returns {field: Korean reason}; empty means the label can be submitted.

    concept_status maps "<scheme>:<code>" to draft/approved/retired. Draft concepts are allowed while the
    ontology is a draft (stage A/B) but retired or unknown codes are not.
    """
    errors: dict[str, str] = {}
    for name, allowed in REQUIRED_FIELDS.items():
        value = getattr(label, name)
        if value is None or value == "":
            errors[name] = "필수 항목입니다"
        elif value not in allowed:
            errors[name] = "허용되지 않는 값입니다"
    for name, limit in MAX_TEXT.items():
        value = getattr(label, name)
        if value is not None and len(value) > limit:
            errors[name] = f"{limit}자 이내로 입력해 주세요"

    revised = (label.revised_answer or "").strip()
    reason = (label.revision_reason or "").strip()
    if label.accuracy in ("minor_edit", "inappropriate"):
        if not revised:
            errors.setdefault("revised_answer", "수정이 필요하다고 평가했으면 수정안을 입력해 주세요")
        if not reason:
            errors.setdefault("revision_reason", "수정 사유를 입력해 주세요")
    if label.reuse_scope in ("conditional", "general_candidate") and not label.revision_evidence:
        errors.setdefault("revision_evidence", "재사용 후보로 지정하려면 근거를 하나 이상 연결해 주세요")
    if label.reuse_scope in ("conditional", "general_candidate") and label.accuracy == "undeterminable":
        errors.setdefault("reuse_scope", "판단 불가 사례는 재사용 후보로 지정할 수 없습니다")
    if label.accuracy == "appropriate" and label.guidance == "inappropriate":
        errors.setdefault("guidance", "내용이 적절한데 안내가 부적절하다는 조합은 함께 쓸 수 없습니다")
    if label.accuracy == "appropriate" and label.safety_issue:
        errors.setdefault("safety_issue", "안전 문제가 있으면 정확성을 '적절'로 둘 수 없습니다")

    seen: set[tuple[str, str]] = set()
    for tag in label.concepts:
        status = concept_status.get(tag.code)
        if status is None:
            errors.setdefault("concepts", f"없는 개념 코드입니다: {tag.code}")
        elif status == "retired":
            errors.setdefault("concepts", f"폐기된 개념 코드입니다: {tag.code}")
        if tag.assertion not in ASSERTIONS:
            errors.setdefault("concepts", f"허용되지 않는 출처 구분입니다: {tag.assertion}")
        key = (tag.code, tag.assertion)
        if key in seen:
            errors.setdefault("concepts", f"같은 개념·출처가 중복되었습니다: {tag.code}")
        seen.add(key)
    return errors


@dataclass
class SubmittedLabel:
    author_kind: str
    reviewer_id: str | None
    accuracy: str
    guidance: str
    reuse_scope: str


def review_status(labels: Iterable[SubmittedLabel]) -> str:
    """Item review status derived from submitted labels. Only vet labels count (operator/ai never change it).

    One vet -> vet_reviewed. Two or more distinct vets agreeing on AGREEMENT_FIELDS -> independently_verified,
    otherwise disputed. A vet's latest label is the one passed in (one label per vet per item).
    """
    vet_labels: dict[str, SubmittedLabel] = {}
    for lab in labels:
        if lab.author_kind == "vet" and lab.reviewer_id:
            vet_labels[lab.reviewer_id] = lab
    if not vet_labels:
        return "draft"
    if len(vet_labels) == 1:
        return "vet_reviewed"
    signatures = {tuple(getattr(lab, f) for f in AGREEMENT_FIELDS) for lab in vet_labels.values()}
    return "independently_verified" if len(signatures) == 1 else "disputed"


def labelers(labels: Iterable[SubmittedLabel]) -> set[str]:
    return {lab.reviewer_id for lab in labels if lab.reviewer_id}


def approval_block_reason(*, approver_role: str, approver_id: str, production: bool, is_mock: bool,
                          item_status: str | None, reuse_scope_ok: bool, item_labelers: set[str]) -> str | None:
    """Why a knowledge candidate cannot be approved now (None = can approve)."""
    if approver_role == "operator":
        if production:
            return "운영 환경에서는 운영자가 발행 승인할 수 없습니다(수의사만)"
        if not is_mock:
            return "운영자 승인은 모의 데이터 시험에만 쓸 수 있습니다"
        return None  # stage A: an operator exercises the flow with mock data in a test environment
    if approver_role != "vet":
        return "승인 권한이 없습니다"
    if item_status != "independently_verified":
        return "독립 재검증이 끝난 사례만 발행 승인할 수 있습니다"
    if not reuse_scope_ok:
        return "재사용 범위가 '조건부' 또는 '일반 지식 후보'인 사례만 발행할 수 있습니다"
    if approver_id in item_labelers:
        return "직접 라벨링한 사례는 다른 수의사가 승인해야 합니다"
    return None


def activation_block_reason(*, publish_status: str, approved_hash: str | None, content_hash: str,
                            eval_passed: bool | None, is_mock: bool, production: bool) -> str | None:
    """Why a candidate cannot be activated (None = can activate)."""
    if is_mock and production:
        return "모의 데이터는 운영 환경에서 활성화할 수 없습니다"
    if publish_status != "approved":
        return "발행 승인된 후보만 활성화할 수 있습니다"
    if approved_hash != content_hash:
        return "승인 후 내용이 바뀌었습니다. 다시 승인해야 합니다"
    if eval_passed is not True:
        return "회귀 평가를 통과해야 활성화할 수 있습니다"
    return None
