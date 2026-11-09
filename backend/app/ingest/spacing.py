"""Conservative Korean spacing fix for the prepared copy (raw files are never touched).

Only one kind of error is fixed: a space wrongly inserted before a word piece that cannot stand
alone — a particle (조사), an ending (어미), a suffix (접미사) or the copula 이다.
    "이상적이 라고" -> "이상적이라고", "보호자와 의" -> "보호자와의", "증가 하게" -> "증가하게"
A space is removed only when EVERY morpheme of the following word is such a bound piece, so a
word that merely starts with one ("투과성 이물질이": 이 + 물질 + 이) is left alone.
Missing spaces are not inserted: a general spacing model was tried (Kiwi.space) and broke medical
terms ("슬개골" -> "슬 개 골", "말티즈" -> "말 티 즈"), so it is not used.

A decimal split before a unit is also joined: "2. 5배" -> "2.5배", "4. 8kg" -> "4.8kg".

Dataset 59 also has a systematic corruption: a syllable 이 <-> 가 swapped and a space inserted after
it ("보이며" -> "보가 며", "다이어트" -> "다가 어트", "닭가슴살" -> "닭이 슴살", "것인가요" -> "것인이 요").
When a corpus is given (set_corpus), such a spot is restored only if BOTH agree:
  - the analyser scores the restored form clearly higher than the text and than just joining it;
  - the restored form occurs in the corpus (>= 3 times) more often than the corrupted form, and the
    merely-joined form is rare (otherwise both readings are real, e.g. "아가 에게" -> "아가에게").
This pass runs before the space-joining pass.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache

BOUND_TAGS = ("J", "E", "XSV", "XSA", "XSN", "VCP")  # particles, endings, verb/adj/noun suffixes, copula
PUNCT_TAGS = ("SF", "SP", "SS", "SE", "SO", "SW")
_NEXT_WS = re.compile(r"\s")
_SWAP_SPOT = re.compile(r"(?<=\S)([가이]) (?=\S)")
_SWAP = {"가": "이", "이": "가"}
SWAP_MIN_SCORE_GAIN = 3.0
SWAP_MIN_CORPUS_COUNT = 3
_DECIMAL = re.compile(r"(?<![\d.])(\d+)\. (\d+)(?=\s?(?:배|kg|g|mg|ml|mL|cc|L|cm|mm|%|℃|도))")


# What may come right before each bound piece (Korean grammar). The analyser sometimes reads an
# unknown medical word as an ending ("다뇨" -> EF), so a bound piece is joined only after a word
# class it can legally follow: never after punctuation, a particle, or an adnominal ending.
_NOUNISH = ("NNG", "NNP", "NNB", "NR", "NP", "SL", "SH", "SN", "XSN", "ETN", "SSC", "XR")
_STEMS = ("VV", "VA", "VX", "VCP", "VCN", "XSV", "XSA", "EP")


def _can_attach(prev: str, cur: str) -> bool:
    prev = prev.split("-")[0]  # irregular-conjugation marks such as "VV-I"
    if cur.startswith("J"):
        return prev in _NOUNISH or (prev.startswith("J") and cur in ("JX", "JKG"))  # 보호자와의, 집에서는
    if cur.startswith("E"):
        return prev in _STEMS
    if cur.startswith(("XSV", "XSA", "XSN", "VCP")):
        return prev in _NOUNISH
    return False


@dataclass
class SpacingChange:
    position: int  # offset of the removed space in the input text
    before: str  # short context, for the audit log
    after: str
    rule: str  # "bound:<tag>", "decimal", "swap" (이<->가, space removed) or "swap_keep_space"


@dataclass
class SpacingFixer:
    _kiwi: object = field(default=None, repr=False)
    _corpus: str | None = field(default=None, repr=False)

    def _tokenize(self, text: str):
        return self._analyser().tokenize(text)

    def _analyser(self):
        if self._kiwi is None:
            from kiwipiepy import Kiwi

            self._kiwi = Kiwi()
        return self._kiwi

    def set_corpus(self, texts) -> None:
        """Enable the 이/가 swap repair, using these texts as frequency evidence."""
        self._corpus = "\n".join(texts)
        self._count = lru_cache(maxsize=None)(self._corpus.count)
        self._score = lru_cache(maxsize=200_000)(lambda seg: self._analyser().analyze(seg, top_n=1)[0][1])

    def _swap_pass(self, text: str) -> tuple[str, list[SpacingChange]]:
        edits: list[tuple[int, str, str]] = []  # (index of the swapped syllable, replacement of text[i:i+2], rule)
        for m in _SWAP_SPOT.finditer(text):
            i = m.start(1)
            s = text.rfind(" ", 0, i)
            s = 0 if s < 0 else s + 1
            e = _NEXT_WS.search(text, i + 2)
            e = e.start() if e else len(text)
            seg, j, c2 = text[s:e], i - s, _SWAP[text[i]]
            variants = {"swap": c2, "swap_keep_space": c2 + " "}
            sc = {k: self._score(seg[:j] + v + seg[j + 2 :]) for k, v in variants.items()}
            base = max(self._score(seg), self._score(seg[:j + 1] + seg[j + 2 :]))
            if max(sc.values()) - base <= SWAP_MIN_SCORE_GAIN:  # cheap check first; corpus counts are slow
                continue
            left, right = text[max(s, i - 2) : i], text[i + 2 : min(e, i + 4)]
            bad = self._count(left + text[i] + " " + right)
            joined = self._count(left + text[i] + right)
            best = None
            for k, v in variants.items():
                good = self._count(left + v + right)
                if (sc[k] - base > SWAP_MIN_SCORE_GAIN and good >= SWAP_MIN_CORPUS_COUNT and good > bad
                        and joined * 5 < good and (best is None or good > best[0])):
                    best = (good, k, v)
            if best:
                edits.append((i, best[2], best[1]))
        if not edits:
            return text, []
        changes, out, last = [], [], 0
        for i, rep, rule in edits:
            out += [text[last:i], rep]
            last = i + 2
            changes.append(SpacingChange(i, text[max(0, i - 6) : i + 8], text[max(0, i - 6) : i] + rep + text[i + 2 : i + 8], rule))
        out.append(text[last:])
        return "".join(out), changes

    def __call__(self, text: str) -> tuple[str, list[SpacingChange]]:
        swapped: list[SpacingChange] = []
        if self._corpus is not None:
            text, swapped = self._swap_pass(text)
        text, joined = self._join_pass(text)
        return text, swapped + joined

    def _join_pass(self, text: str) -> tuple[str, list[SpacingChange]]:
        removals: dict[int, str] = {}
        for m in _DECIMAL.finditer(text):
            removals[m.start(2) - 1] = "decimal"
        toks = self._tokenize(text)
        for i in range(1, len(toks)):
            t, prev = toks[i], toks[i - 1]
            space = t.start - 1
            if space < 0 or text[space] != " ":
                continue
            if prev.start + prev.len > space:  # t is not the first morpheme of its word
                continue
            if not t.len or text[t.start] != t.form[0]:  # morpheme restored by the analyser, not in the text
                continue                                  # ("경우 다뇨" read as 경우 + 이(VCP) + 다뇨)
            if not t.tag.startswith(BOUND_TAGS) or not _can_attach(prev.tag, t.tag):
                continue
            m = _NEXT_WS.search(text, t.start)
            stop = m.start() if m else len(text)
            word = [u for u in toks[i:] if u.start < stop]
            if all(u.tag.startswith(BOUND_TAGS) or u.tag in PUNCT_TAGS for u in word):
                removals.setdefault(space, f"bound:{t.tag}")
        if not removals:
            return text, []
        changes = [SpacingChange(p, text[max(0, p - 8) : p + 8], (text[max(0, p - 8) : p] + text[p + 1 : p + 8]), rule)
                   for p, rule in sorted(removals.items())]
        out = text
        for p in sorted(removals, reverse=True):
            out = out[:p] + out[p + 1 :]
        return out, changes
