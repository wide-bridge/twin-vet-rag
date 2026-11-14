"""Source text cleaning rules (draft, docs/01 §4 진행 방법 2단계). Detection only — nothing is deleted here.

AI Hub qa_cases often carry templated text that is not part of the guardian's question or the vet's answer:
  - question: request sentences about a fixed list of care topics that use the template's own request wording ("샴푸 주기를 단계별로 설명해 주실 수 있을까요
    간단히 알려 주세요."), and pure request phrases ("간단히 알려 주실 수 있을까요 핵심만 알려 주실 수 있을까요?")
  - vet answer: generic care-instruction phrases ("필요하면 계획을 조정해 주세요, 환경 변화를 최소화해 주세요.")

Conservative scope of this rule version:
  - only a *trailing block* is a candidate: walk sentences from the end while every sentence is template text, stop
    at the first one that is not. Template text at the start, in the middle or spliced into a real sentence is left
    alone (reported as 'not covered'). At least one original sentence must remain.
  - answer sentences count as template only when *every* phrase is in the phrase inventory: phrases (split at the
    template endings) seen in at least `min_docs` different answers after removing filler adverbs. A one-off vet
    sentence cannot be in the inventory, even when it reads like care advice.
  - removed answer sentences that carry care/safety wording (vet visit, prescription, stopping) or a topic word of the
    case department are marked 'hold' (판단 보류) instead of 'remove' (삭제 후보).
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Iterable

RULE_VERSION = "clean-draft-2026-11-14.4"

_SENT = re.compile(r"[^.?!]+[.?!]?")

# ---------------------------------------------------------------- question side
# closed topic vocabulary, mined from v2 qa_cases request sentences (topics seen >= 30 times)
Q_TOPICS = (
    "귀·발 관리", "샴푸 주기", "경과 기록 방법", "홈케어 방법", "내원 시점", "진단 필요성", "식이 조절", "환경 관리",
    "피부 진정 방법", "알레르겐 회피", "산책 시 주의", "재발 예방", "약물 복용", "가려움 완화", "영양제 선택",
    "검사 우선순위", "목욕", "통증 신호 구분", "긁음", "보호 장구 사용 요령", "외용제 바르는 순서", "목욕 전 준비",
    "헹굼 시간 기준", "목욕제 선택", "다른 질환과의 감별", "피부 장벽 회복", "경과 사진 남기는 법", "항염 외용제 사용",
    "실내 습도 관리", "세척 후 건조 방법", "약 복용 시간 관리", "운동량 조절", "알레르겐 노출 추적", "스트레스 관리",
    "재발기", "초기 악화 대처", "가려움 점수 기록", "발바닥 관리", "항히스타민 사용", "스테로이드 복용 기준",
    "목욕 빈도 조정", "보습제 선택", "피부 장벽 관리", "진단 후 관리 계획", "식이 일지 작성", "산책 루틴 조절",
    "일상 루틴 점검", "재발 예방 습관", "샴푸 선택 기준", "간식 선택 원칙", "보습 루틴 설정", "귀 관리",
    "가려움 관찰 기록", "알레르기 유발 인자 점검", "스트레스 신호 확인", "외용제 도포 순서", "보호 장구 착용 시간",
    "약욕 선택", "반려견 피부 진정 팁",
)
Q_TOPIC_START = re.compile(r"^(" + "|".join(re.escape(t) for t in sorted(Q_TOPICS, key=len, reverse=True)) + r")"
                           r"(을|를|의|에서|에 관해|에 대해|과|와|은|는|도)?\s")
Q_REQUEST = re.compile(r"(\?|주세요|좋을까요|있을까요|있으신가요|알려|부탁드립니다|좋겠습니다)\s*[.?]?$")
# the template's own request predicates (a topic word + "?" alone is not enough: real questions start that way too)
Q_PREDICATE = re.compile(
    r"(핵심 기준|중요한 포인트|핵심 포인트|중요한 기준|권장 기준|조정 기준|단계별로|짧게 요약|요약해 주|주간 계획으로|주간 일정으로|"
    r"흔한 실수|흔히 하는 실수|지속하려면 어떤 기준|이어 가려면 어떤 기준|어떻게 판단하면 좋을까요|꼭 지켜야 할 점|순서를 정리|"
    r"요점만|핵심만 짚어|놓치기 쉬운|체크리스트 형태로|초보 보호자가 주의할 점|감별 포인트|우선순위를 정하는 기준|"
    r"비슷한 상황에서 주의할 점|일상 루틴에 넣으려면|어떤 기준으로 확인하면|기록으로 남길 때|일상에서 적용하는 방법|"
    r"실천하는 요령|체크해야 할 항목|확인할 항목|집에서 실천할 때|바로 실천할 수 있게)")
Q_MODIFIER = re.compile(
    r"(간단히 (알려|정리해|설명해) 주(실 수 있을까요|세요)|짧게 (설명해|정리해|요약해) 주(실 수 있을까요|시면 좋겠습니다|세요)|"
    r"핵심만 (알려 주실 수 있을까요|부탁드립니다|알려 주세요)|요점 위주로 (설명해 주세요|부탁드립니다)|요점만 알려 주세요|"
    r"주의할 점도 함께 알려 주세요|실행 순서로 (알려|안내해) 주(실 수 있을까요|세요)|바로 실천할 수 있게 정리해 주세요|"
    r"현실적으로 적용하는 요령도 알려 주세요|비슷한 질환과 구분(하는)? 기준도 알려 주(실 수 있을까요|세요)|실패를 줄이는 요령도 알려 주세요)")


def question_rule(s: str) -> str | None:
    if Q_TOPIC_START.search(s) and Q_REQUEST.search(s) and (Q_PREDICATE.search(s) or Q_MODIFIER.search(s)):
        return "q_topic_request"
    if Q_MODIFIER.search(s) and not Q_MODIFIER.sub("", s).strip(" .?,"):
        return "q_modifier_only"
    return None


# ---------------------------------------------------------------- answer side
A_PHRASE_END = re.compile(r"(주세요|주시고|하시고|게 좋습니다|진료가 필요합니다|내원이 필요합니다|조정합니다|권합니다)")
# filler adverbs the template inserts at random positions; removed before comparing phrases
A_FILLER = re.compile(
    r"(꾸준히|서서히|일정하게|무리 없이|무리가 가지 않게|부담없게|부담 없이|안전하게|가능하면|점차적으로|과하지 않게|"
    r"상태를 보며|자극이 없도록|규칙적으로|지속 가능하게|필요한 만큼만|일관되게|천천히)")


def answer_phrases(s: str) -> list[str] | None:
    """Split at the template endings; None when text follows the last ending (not template-shaped)."""
    body = s.strip().rstrip(".?! ")
    pieces, last = [], 0
    for m in A_PHRASE_END.finditer(body):
        pieces.append(body[last:m.end()])
        last = m.end()
    if not pieces or body[last:].strip(" ,"):
        return None
    return pieces


def phrase_key(p: str) -> str:
    return re.sub(r"[\s,.·]", "", A_FILLER.sub("", p))


def mine_answer_inventory(answers: Iterable[str], min_docs: int = 20) -> set[str]:
    """Phrase keys seen in at least min_docs different answers (document frequency)."""
    df: Counter = Counter()
    for a in answers:
        keys = set()
        for s in sentences(a):
            for p in answer_phrases(s) or []:
                k = phrase_key(p)
                if k:
                    keys.add(k)
        df.update(keys)
    return {k for k, n in df.items() if n >= min_docs}


def answer_rule(s: str, inventory: set[str]) -> str | None:
    pieces = answer_phrases(s)
    if pieces and all(phrase_key(p) in inventory for p in pieces):
        return "a_template_phrases"
    return None


HOLD_SAFETY = re.compile(r"(병원|진료|수의사|처방|중단|방문|상담|내원|응급|즉시)")
DEPT_TOPICS = {
    "피부과": re.compile(r"(피부|긁|보습|목욕|외용제|핥음|가려움|샴푸|보호 장구)"),
    "안과": re.compile(r"(눈|안약)"),
    "치과": re.compile(r"(양치|치아|잇몸|구강)"),
}


# ---------------------------------------------------------------- detection
def sentence_spans(text: str) -> list[tuple[int, str]]:
    """(start offset, sentence) in order."""
    out = []
    for m in _SENT.finditer(text):
        s = m.group(0)
        if s.strip():
            out.append((m.start() + len(s) - len(s.lstrip()), s.strip()))
    return out


def sentences(text: str) -> list[str]:
    return [s for _, s in sentence_spans(text)]


@dataclass
class Removal:
    text: str
    rule: str
    decision: str  # remove | hold
    reason: str


@dataclass
class Result:
    kept: str
    removed: list[Removal] = field(default_factory=list)
    not_covered: list[str] = field(default_factory=list)  # template-like sentences left outside the trailing block


def _decide_answer(s: str, department: str | None) -> tuple[str, str]:
    if HOLD_SAFETY.search(s):
        return "hold", "진료·처방·중단·상담 등 주의 문구 포함"
    topic = DEPT_TOPICS.get(department or "")
    if topic and topic.search(s):
        return "hold", f"{department} 사례와 주제가 겹침"
    return "remove", "질문과 무관한 일반 관리 문구"


def detect(text: str, side: str, department: str | None = None, inventory: set[str] | None = None) -> Result:
    """side: 'question' or 'answer' (answer needs the phrase inventory)."""
    spans = sentence_spans(text)
    sents = [s for _, s in spans]
    rule_fn = question_rule if side == "question" else (lambda s: answer_rule(s, inventory or set()))
    start = len(sents)
    while start > 1 and rule_fn(sents[start - 1]):
        start -= 1
    removed = []
    for s in sents[start:]:
        if side == "question":
            removed.append(Removal(s, rule_fn(s) or "", "remove", "원 질문 뒤에 붙은 정형 요청 문장"))
        else:
            decision, why = _decide_answer(s, department)
            removed.append(Removal(s, rule_fn(s) or "", decision, why))
    not_covered = [s for s in sents[:start] if rule_fn(s)]
    kept = text[:spans[start][0]].rstrip() if start < len(sents) else text
    return Result(kept, removed, not_covered)
