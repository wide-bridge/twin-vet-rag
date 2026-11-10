"""Server-side clarification rule (backend/app/rag/pipeline.py: clarification). No DB, no model."""
import pytest

from backend.app.config import Settings
from backend.app.domain.dog import LifeStage
from backend.app.rag.pipeline import UNCLEAR_MAX_CHARS, clarification, parse_age_months, resolve_age, stated_age_months
from backend.app.rag.prompts import TEXT_UNCLEAR, DogInfo

S = Settings(_env_file=None)
BASE = {"question_age_text": "", "question_unclear": False, "clarification_question": ""}


def decide(dog_age, stage, data, question="괜찮을까요?", history=False):
    return clarification({**BASE, **data}, DogInfo("모름", dog_age), stage, S, has_history=history, question=question)


@pytest.mark.parametrize("phrase, months", [
    ("5개월", 5), ("10살", 120), ("두 살 반", 30), ("2년 3개월", 27), ("5세", 60), ("열두 살", 144), ("어제", None),
])
def test_parse_age(phrase, months):
    assert parse_age_months(phrase) == months


@pytest.mark.parametrize("question, months", [
    ("일곱 살 된 허스키가 꼬리를 다쳤어요", 84), ("현재 8년 3개월의 나이에 이르렀습니다", 99),
    ("5개월 된 치와와가", 5), ("10년 넘게 아픈 곳이 없었어요", None), ("4cm 밴드를 삼켰어요", None),
])
def test_stated_age(question, months):
    assert stated_age_months(question) == months


@pytest.mark.parametrize("dog_age, stage, question, phrase, conflict", [
    (48, LifeStage.adult, "5개월 된 치와와가 밴드를 삼켰어요", "5개월", True),    # puppy, 43 months apart
    (48, LifeStage.adult, "10살 된 말티즈 예방접종", "10살", True),               # senior, 72 months apart
    (10, LifeStage.puppy, "1살 반인데 설사를 해요", "1살 반", False),            # other stage, only 8 months apart
    (72, LifeStage.adult, "8살 된 아이가 다리를 절어요", "8살", False),          # senior, exactly 24 months
    (36, LifeStage.adult, "5살인데 다리를 절어요", "5살", False),               # same stage
    (48, LifeStage.adult, "어제부터 두 번 토했어요", "", False),                # no age in the question
    (48, LifeStage.adult, "귀를 자꾸 긁어요", "5개월", False),                  # invented by the model: not in the question
    (24, LifeStage.adult, "10살 된 우리 아이가 물을 많이 마셔요", "", True),     # model missed it: server fallback
    (120, LifeStage.senior, "3개월 된 강아지가 설사를 해요", "", True),
    (48, LifeStage.adult, "3살 때 수술한 다리를 다시 절어요", "", False),        # past age, not "된/인"
])
def test_age_conflict(dog_age, stage, question, phrase, conflict):
    text = decide(dog_age, stage, {"question_age_text": phrase}, question=question)
    assert bool(text) is conflict
    if conflict:
        assert text.startswith("입력하신 나이는 ") and "어느 쪽이 맞는지" in text


def test_unclear_short_question_asks():
    assert decide(48, LifeStage.adult, {"question_unclear": True, "clarification_question": "어떤 점이 궁금하신가요?"}) \
        == "어떤 점이 궁금하신가요?"


def test_unclear_flag_ignored_for_long_question():
    long_q = "어제 밤에 거즈 밴드를 삼켰는데 오늘은 밥도 잘 먹고 활기차요. 아직 변으로 안 나왔는데 괜찮을까요? 걱정돼요."
    assert len(long_q) > UNCLEAR_MAX_CHARS
    assert decide(48, LifeStage.adult, {"question_unclear": True, "clarification_question": "체중은요?"}, question=long_q) is None


def test_unclear_flag_ignored_with_history_and_fallback_text():
    assert decide(48, LifeStage.adult, {"question_unclear": True, "clarification_question": "무엇이요?"}, history=True) is None
    assert decide(48, LifeStage.adult, {"question_unclear": True, "clarification_question": " "}) == TEXT_UNCLEAR


@pytest.mark.parametrize("basis, asks, search_stage", [
    ("day", True, LifeStage.adult), ("month", True, LifeStage.adult),          # birth date: trust it, ask
    ("year", False, LifeStage.senior), ("estimate", False, LifeStage.senior),  # approximate: follow the question
])
def test_conflict_depends_on_age_basis(basis, asks, search_stage):
    dog = DogInfo("모름", 48, age_basis=basis)
    text, stage = resolve_age({**BASE, "question_age_text": "10살"}, dog, LifeStage.adult, S, "10살 된 말티즈가 기침해요")
    assert bool(text) is asks and stage is search_stage
