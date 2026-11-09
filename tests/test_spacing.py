"""Spacing fix on SYNTHETIC sentences (patterns taken from errors seen in dataset 59)."""
import pytest

pytest.importorskip("kiwipiepy")

from backend.app.ingest.spacing import SpacingFixer  # noqa: E402

fix = SpacingFixer()


@pytest.mark.parametrize("before, after", [
    ("이상적이 라고 알고 있습니다.", "이상적이라고 알고 있습니다."),
    ("보호자와 의 관계에서 중요합니다.", "보호자와의 관계에서 중요합니다."),
    ("체중이 증가 하였습니다.", "체중이 증가하였습니다."),
    ("가장 효과적이 고 안전합니다.", "가장 효과적이고 안전합니다."),
    ("치료가 효과 를 보았습니다.", "치료가 효과를 보았습니다."),
    ("수컷이 2. 5배 더 큽니다.", "수컷이 2.5배 더 큽니다."),
    ("체중 4. 8kg입니다.", "체중 4.8kg입니다."),
])
def test_detached_bound_pieces_are_joined(before, after):
    fixed, changes = fix(before)
    assert fixed == after and changes


@pytest.mark.parametrize("text", [
    "방사선 투과성 이물질이 보입니다.",  # 이물질 starts with 이 but is a noun
    "슬개골 탈구가 의심됩니다.",
    "말티즈 강아지가 구토를 해요.",
    "즉시 동물병원에 방문하세요.",
    "1. 5분 정도 산책하세요.",
    # '다뇨' (polyuria) is misread as an ending by the analyser
    "2시간마다(심한 다뇨의 경우 1시간마다) 확인한다.",
    "자궁축농증은 다뇨, 구토를 보입니다.",
    "검사가 필요합니다. 다뇨가 나타나면 알려 주세요.",
    "강아지의 다뇨 증상이 있어요.",
    "자궁축농증의 경우 다뇨, 구토를 보입니다.",
    "약 복용 후 다뇨 증상이 있어요.",
    "그런데 현재 다뇨 증상이 있어요.",  # list number, not a decimal before a unit
])
def test_correct_text_is_left_alone(text):
    assert fix(text) == (text, [])


def test_missing_spaces_are_not_inserted():
    assert fix("동물병원에방문하세요")[0] == "동물병원에방문하세요"


def _with_corpus(*texts):
    f = SpacingFixer()
    f.set_corpus(list(texts))
    return f


def test_swapped_syllable_restored_with_corpus_evidence():
    f = _with_corpus(*["구토를 보이며 힘이 없어요.", "다이어트가 필요해요.", "닭가슴살을 줬어요.",
                       "외이염을 앓아요.", "무엇인가요?", "발가락이 빨개요."] * 3)
    assert f("설사를 보가 며 식사도 하지 않아요.")[0] == "설사를 보이며 식사도 하지 않아요."
    assert f("다가 어트가 필요할까요?")[0] == "다이어트가 필요할까요?"
    assert f("닭이 슴살을 줘도 되나요?")[0] == "닭가슴살을 줘도 되나요?"
    assert f("만성 외가 염을 치료했어요.")[0] == "만성 외이염을 치료했어요."
    assert f("원인은 무엇인이 요?")[0] == "원인은 무엇인가요?"
    assert f("발이 락이 부었어요.")[0] == "발가락이 부었어요."


def test_swap_needs_corpus_evidence():
    f = _with_corpus("관계없는 문장입니다.")
    assert f("설사를 보가 며 식사도 하지 않아요.")[0] == "설사를 보가며 식사도 하지 않아요."  # only the join
    f = _with_corpus(*["강아지가 건사료를 먹어요."] * 5)
    assert f("강아지가 건사료를 먹어요.") == ("강아지가 건사료를 먹어요.", [])


def test_swap_skipped_when_both_readings_are_real():
    # "아가에게" (baby) is common on its own, so "아가 에게" is only joined, not turned into "아이에게"
    f = _with_corpus(*(["우리 아가에게 맞춘 조언"] * 4 + ["아이에게 좋아요"] * 6))
    assert f("우리 아가 에게 맞춘 조언")[0] == "우리 아가에게 맞춘 조언"
