"""Mock review bundles for stage A (docs/01 §4). Synthetic, clearly marked, never activated in production.

Each case carries an AI pre-label so the 'AI 제안' path can be exercised without an LLM call.
"""
from __future__ import annotations

MOCK_PREFIX = "[모의] "

MOCK_CASES: list[dict] = [
    {
        "ref": "mock-001",
        "question": "3일째 하루 2~3번 토해요. 밥은 잘 먹어요.",
        "history": [],
        "dog": {"breed": "말티즈", "age_months": 60, "weight_kg": 3.2},
        "ai_answer": "하루 2~3번 구토가 3일 이어지면 위장 자극 가능성이 있습니다. 반나절 금식 후 소량씩 급여해 보시고, "
                     "혈변·무기력·탈수가 보이면 동물병원 진료를 권합니다.",
        "response_type": "grounded",
        "citations": [{"n": 1, "title": "[모의] 내과 상담 사례"}, {"n": 2, "title": "[모의] 소화기 질환 교재"}],
        "show_ai_suggestion": True,
        "ai_label": {"accuracy": "minor_edit", "evidence_support": "supported", "sufficiency": "answerable",
                     "guidance": "missing_important", "reuse_scope": "conditional",
                     "revised_answer": "3일째 반복되는 구토는 집에서 지켜보기보다 동물병원 진료를 권합니다.",
                     "revision_reason": "구토가 3일째로 길어 내원 권유가 더 분명해야 함", "revision_evidence": ["1"],
                     "concepts": [["symptom:vomiting", "owner_reported"], ["observation:duration_days", "owner_reported"],
                                  ["action:vet_visit_soon", "ai_inferred"]]},
    },
    {
        "ref": "mock-002",
        "question": "귀를 자꾸 긁고 머리를 흔들어요. 냄새도 나요.",
        "history": [],
        "dog": {"breed": "푸들", "age_months": 30},
        "ai_answer": "귀를 긁고 머리를 흔들며 냄새가 나면 외이도 염증이 있을 수 있습니다. 귀 세정은 진찰 후에 하시고 "
                     "동물병원에서 귀 검사를 받아 보세요.",
        "response_type": "grounded",
        "citations": [{"n": 1, "title": "[모의] 피부과 상담 사례"}],
        "show_ai_suggestion": False,  # hidden: measures whether vets follow the AI suggestion
        "ai_label": {"accuracy": "appropriate", "evidence_support": "supported", "sufficiency": "answerable",
                     "guidance": "appropriate", "reuse_scope": "case_only",
                     "concepts": [["symptom:itching", "owner_reported"], ["symptom:head_shaking", "owner_reported"],
                                  ["symptom:ear_discharge", "owner_reported"]]},
    },
    {
        "ref": "mock-003",
        "question": "소변을 보려고 계속 자세를 잡는데 안 나와요.",
        "history": [],
        "dog": {"breed": "슈나우저", "age_months": 96, "weight_kg": 8.0},
        "ai_answer": "자주 소변 자세를 취하는 것은 방광염일 수 있습니다. 물을 충분히 주고 하루 이틀 지켜보세요.",
        "response_type": "grounded",
        "citations": [{"n": 1, "title": "[모의] 비뇨기 상담 사례"}],
        "show_ai_suggestion": True,
        "ai_label": {"accuracy": "inappropriate", "evidence_support": "partial", "sufficiency": "needs_more_info",
                     "guidance": "inappropriate", "reuse_scope": "excluded", "safety_issue": True,
                     "revised_answer": "소변이 전혀 나오지 않으면 요도 폐색일 수 있어 바로 동물병원에 연락해야 합니다.",
                     "revision_reason": "배뇨 불가는 응급일 수 있는데 관찰을 권함", "revision_evidence": ["1"],
                     "concepts": [["symptom:straining_urination", "owner_reported"],
                                  ["action:emergency_visit", "vet_assessed"]]},
    },
    {
        "ref": "mock-004",
        "question": "기침을 거위 소리처럼 해요. 흥분하면 더 심해요.",
        "history": [{"role": "user", "content": "기침을 해요"}, {"role": "assistant", "content": "언제부터, 어떤 소리인지 알려 주세요."}],
        "dog": {"breed": "포메라니안", "age_months": 84, "weight_kg": 2.8},
        "ai_answer": "흥분할 때 심해지는 거위 소리 기침은 소형견에서 기관이 좁아지는 경우에 보일 수 있습니다. "
                     "목줄 대신 가슴줄을 쓰고 체중을 관리하며, 잇몸이 파래지거나 숨이 가쁘면 바로 내원하세요.",
        "response_type": "grounded",
        "citations": [{"n": 1, "title": "[모의] 호흡기 교재"}, {"n": 2, "title": "[모의] 내과 상담 사례"}],
        "show_ai_suggestion": True,
        "ai_label": {"accuracy": "appropriate", "evidence_support": "supported", "sufficiency": "answerable",
                     "guidance": "appropriate", "reuse_scope": "general_candidate", "revision_evidence": ["1", "2"],
                     "concepts": [["symptom:coughing", "owner_reported"], ["condition:tracheal_collapse", "ai_inferred"]]},
    },
]

MOCK_CANDIDATE = {
    "ref": "mock-004",
    "kind": "general",
    "title": MOCK_PREFIX + "소형견의 거위 소리 기침 안내",
    "content": "흥분할 때 심해지는 거위 소리 기침은 소형견에서 기관 허탈과 관련될 수 있다. 가슴줄 사용과 체중 관리를 "
               "권하고, 잇몸이 파래지거나 호흡이 가쁘면 즉시 내원을 안내한다.",
    "conditions": {"breed_size": "소형견"},
    "evidence": ["[모의] 호흡기 교재"],
}
