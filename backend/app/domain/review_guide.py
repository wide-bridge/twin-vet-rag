"""Labeling guideline 0.1 draft (docs/01 §4: separate from the ontology).

Shown as help text on the labeling screen (S12) and used in the vet recruitment kit. Draft until a vet
confirms it (stage B). Keys must match backend.app.domain.review value lists.
"""
from __future__ import annotations

GUIDE_VERSION = "0.1-draft (2026-11-13)"

FIELDS: dict[str, dict] = {
    "accuracy": {
        "label": "내용의 정확성",
        "help": "AI 답변의 의학적 내용이 맞는지. 근거 문서와 별개로 수의학적 타당성을 봅니다.",
        "options": {
            "appropriate": ("적절", "그대로 보호자에게 안내해도 되는 내용"),
            "minor_edit": ("일부 수정", "큰 방향은 맞지만 고쳐야 할 표현·누락이 있음. 수정안 필수"),
            "inappropriate": ("부적절", "틀리거나 오해를 부르는 내용이 있음. 수정안 필수"),
            "undeterminable": ("판단 불가", "정보가 부족하거나 진찰 없이는 판단할 수 없음"),
        },
    },
    "evidence_support": {
        "label": "근거의 적합성",
        "help": "인용된 근거 문서가 답변 내용을 실제로 뒷받침하는지.",
        "options": {
            "supported": ("뒷받침됨", "핵심 내용이 근거에 있음"),
            "partial": ("일부만", "일부 내용만 근거에 있음"),
            "unsupported": ("근거 없음·충돌", "근거에 없거나 근거와 다름"),
        },
    },
    "sufficiency": {
        "label": "정보의 충분성",
        "help": "질문에 담긴 정보만으로 답할 수 있었는지.",
        "options": {
            "answerable": ("답변 가능", "주어진 정보로 안내할 수 있음"),
            "needs_more_info": ("추가 질문 필요", "먼저 물어봤어야 할 정보가 있음"),
        },
    },
    "guidance": {
        "label": "안내의 적절성",
        "help": "집에서 관찰·관리, 내원 권유, 응급 안내 수준이 상황에 맞는지.",
        "options": {
            "appropriate": ("적절", "안내 수준이 상황에 맞음"),
            "missing_important": ("중요한 안내 누락", "내원 권유·위험 신호 안내 등이 빠짐"),
            "inappropriate": ("부적절한 안내", "과도하거나 위험한 안내가 있음"),
        },
    },
    "safety_issue": {
        "label": "안전 문제",
        "help": "이 답변을 따르면 반려견에게 해가 될 수 있으면 체크합니다. 체크하면 정확성을 '적절'로 둘 수 없습니다.",
    },
    "revised_answer": {"label": "수정안", "help": "보호자에게 이렇게 안내해야 한다는 답변. 진단·처방 표현은 피합니다."},
    "revision_reason": {"label": "수정 사유", "help": "무엇이 왜 틀렸거나 빠졌는지."},
    "revision_evidence": {"label": "수정 근거", "help": "인용 근거 번호, 교과서·가이드라인 등 출처. 재사용 후보로 지정하면 필수."},
    "reuse_scope": {
        "label": "재사용 범위",
        "help": "'답변이 적절한가'와 별개로, 이 사례를 다른 보호자에게도 쓸 수 있는지.",
        "options": {
            "case_only": ("이 사례에만", "이 반려견·상황에만 해당"),
            "conditional": ("조건부 재사용", "적용 조건(나이·품종·기간 등)을 붙이면 재사용 가능"),
            "general_candidate": ("일반 지식 후보", "조건 없이 일반 지식으로 쓸 수 있음. 독립 재검증 필수"),
            "excluded": ("제외", "지식으로 쓰지 않음"),
        },
    },
    "concepts": {
        "label": "개념 태그",
        "help": "증상·부위·질환·검사·권고를 고르고, 그 정보가 어디서 왔는지 구분합니다.",
        "assertions": {
            "owner_reported": "보호자가 말함",
            "ai_inferred": "AI가 추정함",
            "vet_assessed": "수의사가 검토함",
            "test_confirmed": "검사로 확인됨",
        },
    },
}

DISPUTE_TYPES = {
    "info_lacking": "정보 부족 — 질문만으로는 판단이 갈림",
    "evidence_conflict": "근거 충돌 — 참고 자료끼리 다름",
    "acceptable_difference": "허용 가능한 견해 차이 — 둘 다 타당함",
}
