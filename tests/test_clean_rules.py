"""Cleaning detection rules (read-only draft): trailing block only, real sentences never matched, hold decisions."""
from backend.app.ingest import clean_rules as cr

TEMPLATE = "필요하면 계획을 조정해 주세요, 환경 변화를 최소화해 주세요."
INV = cr.mine_answer_inventory([TEMPLATE + " 불편하면 강도는 낮춰 주세요. 증상이 길어지면 병원 상담을 권합니다."] * 3, min_docs=3)


def test_answer_trailing_block_only():
    a = f"구토가 반복되면 탈수를 확인하세요. {TEMPLATE} 원래 수의사 결론 문장입니다. {TEMPLATE} 불편하면 강도는 낮춰 주세요."
    r = cr.detect(a, "answer", "내과", INV)
    assert [x.text for x in r.removed] == [TEMPLATE, "불편하면 강도는 낮춰 주세요."]
    assert r.kept.endswith("원래 수의사 결론 문장입니다.")
    assert r.not_covered == [TEMPLATE]  # the middle copy is left alone and reported


def test_real_sentence_is_never_template_and_one_sentence_remains():
    real = "또한, 탈수를 예방하기 위해 신선한 물을 충분히 마실 수 있도록 해주세요."
    assert cr.answer_rule(real, INV) is None
    r = cr.detect(TEMPLATE, "answer", "내과", INV)  # whole answer is template: keep it
    assert r.removed == [] and r.kept == TEMPLATE


def test_filler_adverbs_do_not_hide_template():
    assert cr.answer_rule("필요하면 서서히 계획을 조정해 주세요, 환경 변화를 꾸준히 최소화해 주세요.", INV)


def test_hold_for_safety_or_department_topic():
    a = "원래 결론입니다. 증상이 길어지면 병원 상담을 권합니다."
    assert cr.detect(a, "answer", "내과", INV).removed[0].decision == "hold"
    inv = cr.mine_answer_inventory(["목욕 후에는 보습을 충분히 해 주세요."] * 3, min_docs=3)
    r = cr.detect("원래 결론입니다. 목욕 후에는 보습을 충분히 해 주세요.", "answer", "피부과", inv)
    assert r.removed[0].decision == "hold"
    assert cr.detect("원래 결론입니다. 목욕 후에는 보습을 충분히 해 주세요.", "answer", "내과", inv).removed[0].decision == "remove"


def test_question_rules():
    q = ("저희 강아지가 3일째 토해요. 병원에 가야 할까요? 샴푸 주기를 단계별로 설명해 주실 수 있을까요 간단히 알려 주세요. "
         "간단히 알려 주실 수 있을까요 핵심만 부탁드립니다.")
    r = cr.detect(q, "question")
    assert [x.rule for x in r.removed] == ["q_topic_request", "q_modifier_only"]
    assert r.kept == "저희 강아지가 3일째 토해요. 병원에 가야 할까요?"
    # a real sentence with a spliced modifier is not 'modifier only'
    assert cr.question_rule("이에 대한 전문적인 의견을 여쭙고 싶습니다 핵심만 부탁드립니다.") is None
    # a topic word inside a real question does not start a template sentence
    assert cr.question_rule("어제 목욕을 시킨 뒤로 계속 긁는데 괜찮을까요?") is None


def test_topic_word_question_from_a_guardian_is_not_template():
    for s in ("목욕을 시킨 뒤로 계속 긁는데 괜찮을까요?", "재발 예방을 위해 사료를 바꿔야 할까요?",
              "내원 시점을 놓친 것 같은데 지금 가도 될까요?", "귀 관리는 어떻게 해야 하나요?"):
        assert cr.question_rule(s) is None, s
    assert cr.question_rule("샴푸 주기를 단계별로 설명해 주실 수 있을까요?") == "q_topic_request"
    assert cr.question_rule("귀·발 관리를 집에서 실천할 때 주의할 점이 있을까요 간단히 알려 주세요.") == "q_topic_request"


def test_cleaned_text_keeps_hold_sentences():
    a = "원래 결론입니다. 증상이 길어지면 병원 상담을 권합니다. 불편하면 강도는 낮춰 주세요."
    r = cr.detect(a, "answer", "내과", INV)
    assert [x.decision for x in r.removed] == ["hold", "remove"]
    assert cr.cleaned(a, r) == "원래 결론입니다. 증상이 길어지면 병원 상담을 권합니다."
