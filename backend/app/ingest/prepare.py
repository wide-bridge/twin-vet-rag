"""Local preparation of AI Hub dataset 59 (no network, no paid API calls).

Produces versioned JSONL files for: qa_case chunks (Training), reference chunks (Training),
and the fixed evaluation sets (Validation QA, split into dev / final).
Raw files are read-only; nothing under AIHUB_DATA_DIR is modified.
"""
from __future__ import annotations

import hashlib
import json
import random
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Iterator

from ..domain.dog import SOURCE_LIFECYCLE_MAP
from .spacing import SpacingFixer

DATASET_CODE = "aihub59"
DATASET_DIRNAME = "59.반려견 성장 및 질병 관련 말뭉치 데이터"
EXTRACTED_SUBPATH = Path("extracted") / "3.개방데이터" / "1.데이터"
EMBEDDING_MAX_TOKENS = 8191  # text-embedding-3-small input limit
UNSPECIFIED_DISEASE = "기타"

_WS_RE = re.compile(r"[ \t 　]+")
_MULTI_NL_RE = re.compile(r"\n{3,}")
_PHONE_RE = re.compile(r"(?<!\d)01[016789][-\s.]?\d{3,4}[-\s.]?\d{4}(?!\d)")
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")


# ---------------------------------------------------------------- text helpers
def normalize_text(text: str, *, unescape_literal_newlines: bool = False) -> str:
    """Whitespace normalisation. Literal '\\n' is converted only when explicitly requested
    (reference body field), never as a blanket unescape over whole JSON documents."""
    if unescape_literal_newlines:
        text = text.replace("\\r\\n", "\n").replace("\\n", "\n")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [_WS_RE.sub(" ", ln).strip() for ln in text.split("\n")]
    return _MULTI_NL_RE.sub("\n\n", "\n".join(lines)).strip()


def mask_pii(text: str) -> tuple[str, int]:
    count = 0

    def _sub(token: str):
        def repl(_m: re.Match) -> str:
            nonlocal count
            count += 1
            return token

        return repl

    text = _PHONE_RE.sub(_sub("[전화번호]"), text)
    text = _EMAIL_RE.sub(_sub("[이메일]"), text)
    return text, count


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def dedup_key(text: str) -> str:
    """Key for exact-duplicate detection: ignores whitespace and punctuation differences."""
    return sha256(re.sub(r"[\W_]+", "", text).lower())


class TokenCounter:
    def __init__(self) -> None:
        import tiktoken

        self._enc = tiktoken.get_encoding("cl100k_base")  # encoding of text-embedding-3-small

    def __call__(self, text: str) -> int:
        return len(self._enc.encode(text, disallowed_special=()))


# ---------------------------------------------------------------- source discovery
@dataclass
class SourceFile:
    path: Path
    split: str  # "train" | "valid"
    kind: str  # "qa" | "reference"
    relative_path: str  # relative to the dataset root, never absolute


def iter_source_files(dataset_root: Path) -> Iterator[SourceFile]:
    base = dataset_root / EXTRACTED_SUBPATH
    layout = {
        ("Training", "02.라벨링데이터"): ("train", "qa"),
        ("Validation", "02.라벨링데이터"): ("valid", "qa"),
        ("Training", "01.원천데이터"): ("train", "reference"),
        ("Validation", "01.원천데이터"): ("valid", "reference"),
    }
    for (split_dir, kind_dir), (split, kind) in layout.items():
        folder = base / split_dir / kind_dir
        if not folder.is_dir():
            continue
        for p in sorted(folder.rglob("*.json")):
            yield SourceFile(p, split, kind, p.relative_to(dataset_root).as_posix())


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


# ---------------------------------------------------------------- QA records
@dataclass
class QARecord:
    source_record_id: str
    split: str
    relative_path: str
    department: str
    disease_raw: str
    lifecycle_raw: str
    question: str
    answer: str
    pii_masked: int = 0

    @property
    def life_stages(self) -> list[str]:
        stage = SOURCE_LIFECYCLE_MAP.get(self.lifecycle_raw)
        return [stage.value] if stage else []

    @property
    def disease_names(self) -> list[str]:
        d = self.disease_raw
        return [] if not d or d == UNSPECIFIED_DISEASE else [d]

    @property
    def content(self) -> str:
        # instruction (role prompt for the original fine-tuning set) is deliberately excluded
        return f"보호자 질문: {self.question}\n수의사 답변: {self.answer}"


def parse_qa(sf: SourceFile) -> QARecord:
    raw = _load_json(sf.path)
    meta, qa = raw.get("meta") or {}, raw.get("qa") or {}
    question, n1 = mask_pii(normalize_text(str(qa.get("input") or "")))
    answer, n2 = mask_pii(normalize_text(str(qa.get("output") or "")))
    return QARecord(
        source_record_id=sf.path.stem,
        split=sf.split,
        relative_path=sf.relative_path,
        department=str(meta.get("department") or "").strip(),
        disease_raw=str(meta.get("disease") or "").strip(),
        lifecycle_raw=str(meta.get("lifeCycle") or "").strip(),
        question=question,
        answer=answer,
        pii_masked=n1 + n2,
    )


# ---------------------------------------------------------------- reference records & chunking
@dataclass
class ReferenceRecord:
    source_record_id: str
    split: str
    relative_path: str
    title: str
    author: str
    publisher: str
    department: str
    body: str
    literal_newlines: int = 0
    pii_masked: int = 0


def parse_reference(sf: SourceFile) -> ReferenceRecord:
    raw = _load_json(sf.path)
    body_raw = str(raw.get("disease") or "")  # in the source schema the body text lives in `disease`
    literal = body_raw.count("\\n")
    body, n = mask_pii(normalize_text(body_raw, unescape_literal_newlines=True))
    return ReferenceRecord(
        source_record_id=sf.path.stem,
        split=sf.split,
        relative_path=sf.relative_path,
        title=normalize_text(str(raw.get("title") or "")),
        author=normalize_text(str(raw.get("author") or "")),
        publisher=normalize_text(str(raw.get("publisher") or "")),
        department=str(raw.get("department") or "").strip(),
        body=body,
        literal_newlines=literal,
        pii_masked=n,
    )


@dataclass
class Chunk:
    ordinal: int
    text: str
    start: int  # char offsets into the normalised body (locator)
    end: int
    tokens: int


def chunk_paragraphs(body: str, count_tokens, target_tokens: int = 500, max_tokens: int = 800, overlap_paragraphs: int = 1) -> list[Chunk]:
    """Pack paragraphs (split on newlines) into ~target_tokens chunks; overlong paragraphs are
    split on sentence ends. Consecutive chunks share `overlap_paragraphs` trailing paragraph(s)."""
    units: list[tuple[int, int]] = []  # (start, end) spans in body
    pos = 0
    for para in body.split("\n"):
        start, end = pos, pos + len(para)
        pos = end + 1
        if not para.strip():
            continue
        if count_tokens(para) <= max_tokens:
            units.append((start, end))
            continue
        s = start
        for m in re.finditer(r"(?<=[.!?。])\s+|(?<=다\.)\s*", para):
            cut = start + m.end()
            if count_tokens(body[s:cut]) >= target_tokens:
                units.append((s, cut))
                s = cut
        if s < end:
            # hard split as a last resort (no sentence boundary found)
            while count_tokens(body[s:end]) > max_tokens:
                cut = s + max(200, (end - s) // 2)
                units.append((s, cut))
                s = cut
            units.append((s, end))

    chunks: list[Chunk] = []
    i = 0
    while i < len(units):
        j, tokens = i, 0
        while j < len(units):
            t = count_tokens(body[units[j][0] : units[j][1]])
            if j > i and tokens + t > target_tokens:
                break
            tokens += t
            j += 1
        start, end = units[i][0], units[j - 1][1]
        text = body[start:end].strip()
        chunks.append(Chunk(len(chunks), text, start, end, count_tokens(text)))
        if j >= len(units):
            break
        i = max(i + 1, j - overlap_paragraphs)
    return chunks


# ---------------------------------------------------------------- orchestration
@dataclass
class PrepareResult:
    counts: Counter = field(default_factory=Counter)
    report: dict = field(default_factory=dict)


def _write_jsonl(path: Path, rows: Iterable[dict]) -> int:
    n = 0
    with path.open("w", encoding="utf-8", newline="\n") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
            n += 1
    return n


def _stratified_split(records: list[QARecord], dev_ratio: float, seed: int) -> tuple[list[QARecord], list[QARecord]]:
    by_dept: dict[str, list[QARecord]] = defaultdict(list)
    for r in sorted(records, key=lambda r: r.source_record_id):
        by_dept[r.department].append(r)
    rng = random.Random(seed)
    dev, final = [], []
    for dept in sorted(by_dept):
        items = by_dept[dept]
        rng.shuffle(items)
        k = round(len(items) * dev_ratio)
        dev.extend(items[:k])
        final.extend(items[k:])
    return dev, final


def prepare(dataset_root: Path, out_dir: Path, *, seed: int = 20261109, dev_ratio: float = 0.25, count_tokens=None,
            fix_spacing=None) -> PrepareResult:
    """fix_spacing: callable text -> (text, changes); default SpacingFixer. Applied to the indexed
    Training text only (QA question/answer, reference body). Validation/eval text is kept as typed."""
    count_tokens = count_tokens or TokenCounter()
    fix_spacing = fix_spacing or SpacingFixer()
    spacing_log: list[dict] = []

    def fix(text: str, source_record_id: str, field_name: str) -> tuple[str, int]:
        fixed, changes = fix_spacing(text)
        for c in changes:
            spacing_log.append({"source_record_id": source_record_id, "field": field_name, "rule": c.rule,
                                "before": c.before, "after": c.after})
        return fixed, len(changes)
    out_dir.mkdir(parents=True, exist_ok=True)
    res = PrepareResult()
    excluded: list[dict] = []

    qa_train, qa_valid, ref_train, ref_valid = [], [], [], []
    for sf in iter_source_files(dataset_root):
        try:
            if sf.kind == "qa":
                rec = parse_qa(sf)
                (qa_train if sf.split == "train" else qa_valid).append(rec)
            else:
                rec = parse_reference(sf)
                (ref_train if sf.split == "train" else ref_valid).append(rec)
        except (json.JSONDecodeError, UnicodeDecodeError, AttributeError) as e:
            excluded.append({"relative_path": sf.relative_path, "reason": f"parse_error:{type(e).__name__}"})

    # corpus evidence for the 이/가 swap repair: all indexed Training text, as parsed
    if hasattr(fix_spacing, "set_corpus"):
        fix_spacing.set_corpus([x for r in qa_train for x in (r.question, r.answer)] + [r.body for r in ref_train])

    # ---- Training QA -> qa_case (1 case = 1 chunk), exact-duplicate removal
    seen: dict[str, str] = {}
    qa_rows, qa_tokens, qa_over = [], [], 0
    for r in sorted(qa_train, key=lambda r: r.source_record_id):
        if not r.question or not r.answer:
            excluded.append({"source_record_id": r.source_record_id, "split": "train", "reason": "empty_question_or_answer"})
            continue
        key = dedup_key(r.question + "||" + r.answer)
        if key in seen:
            excluded.append({"source_record_id": r.source_record_id, "split": "train", "reason": "duplicate_in_train", "duplicate_of": seen[key]})
            continue
        seen[key] = r.source_record_id
        r.question, nq = fix(r.question, r.source_record_id, "question")
        r.answer, na = fix(r.answer, r.source_record_id, "answer")
        tokens = count_tokens(r.content)
        if tokens > EMBEDDING_MAX_TOKENS:
            # not truncated or split automatically (docs/03 §5) — reported for review
            qa_over += 1
            excluded.append({"source_record_id": r.source_record_id, "split": "train", "reason": "over_embedding_limit", "tokens": tokens})
            continue
        qa_tokens.append(tokens)
        qa_rows.append({
            "document_type": "qa_case",
            "dataset_code": DATASET_CODE,
            "source_record_id": r.source_record_id,
            "source_relative_path": r.relative_path,
            "split": "train",
            "title": f"{r.department} 상담 사례" + (f" · {r.disease_names[0]}" if r.disease_names else ""),
            "department": r.department,
            "disease_names": r.disease_names,
            "life_stages": r.life_stages,
            "content": r.content,
            "content_hash": sha256(r.content),
            "tokens": tokens,
            "metadata": {"lifeCycle": r.lifecycle_raw, "disease": r.disease_raw, "pii_masked": r.pii_masked,
                         "spacing_fixes": nq + na},
        })

    # ---- Training reference -> chunks (Validation reference is not indexed; reported only)
    ref_rows, ref_docs, ref_tokens = [], [], []
    ref_seen: dict[str, str] = {}
    for r in sorted(ref_train, key=lambda r: r.source_record_id):
        if not r.body:
            excluded.append({"source_record_id": r.source_record_id, "split": "train", "reason": "empty_reference_body"})
            continue
        key = dedup_key(r.body)
        if key in ref_seen:
            excluded.append({"source_record_id": r.source_record_id, "split": "train", "reason": "duplicate_reference", "duplicate_of": ref_seen[key]})
            continue
        ref_seen[key] = r.source_record_id
        r.body, nb = fix(r.body, r.source_record_id, "body")  # locators below refer to this corrected copy
        chunks = chunk_paragraphs(r.body, count_tokens)
        ref_docs.append({
            "document_type": "reference",
            "dataset_code": DATASET_CODE,
            "source_record_id": r.source_record_id,
            "source_relative_path": r.relative_path,
            "split": "train",
            "title": r.title,
            "author": r.author,
            "publisher": r.publisher,
            "department": r.department,
            "disease_names": [],
            "life_stages": [],  # not labelled in the source; not guessed
            "content_hash": sha256(r.body),
            "chars": len(r.body),
            "chunks": len(chunks),
            "metadata": {"literal_newlines_converted": r.literal_newlines, "pii_masked": r.pii_masked, "spacing_fixes": nb},
        })
        for c in chunks:
            ref_tokens.append(c.tokens)
            ref_rows.append({
                "document_type": "reference",
                "source_record_id": r.source_record_id,
                "ordinal": c.ordinal,
                "locator": {"char_start": c.start, "char_end": c.end},
                "content": c.text,
                "content_hash": sha256(c.text),
                "tokens": c.tokens,
            })

    # ---- Validation QA -> evaluation sets; never indexed
    train_q_keys = {dedup_key(r.question) for r in qa_train if r.question}
    train_qa_keys = set(seen)
    eval_pool: list[QARecord] = []
    valid_seen: set[str] = set()
    for r in sorted(qa_valid, key=lambda r: r.source_record_id):
        if not r.question or not r.answer:
            excluded.append({"source_record_id": r.source_record_id, "split": "valid", "reason": "empty_question_or_answer"})
            continue
        qk = dedup_key(r.question)
        if dedup_key(r.question + "||" + r.answer) in train_qa_keys or qk in train_q_keys:
            excluded.append({"source_record_id": r.source_record_id, "split": "valid", "reason": "leak_duplicate_of_train"})
            continue
        if qk in valid_seen:
            excluded.append({"source_record_id": r.source_record_id, "split": "valid", "reason": "duplicate_in_valid"})
            continue
        valid_seen.add(qk)
        eval_pool.append(r)
    dev, final = _stratified_split(eval_pool, dev_ratio, seed)

    def eval_row(r: QARecord, subset: str) -> dict:
        return {
            "eval_id": f"{DATASET_CODE}-{subset}-{r.source_record_id}",
            "subset": subset,
            "source_record_id": r.source_record_id,
            "source_relative_path": r.relative_path,
            "department": r.department,
            "disease_names": r.disease_names,
            "life_stages": r.life_stages,
            "question": r.question,
            "reference_answer": r.answer,
            "question_hash": sha256(r.question),
        }

    files = {
        "qa_cases.jsonl": qa_rows,
        "reference_docs.jsonl": ref_docs,
        "reference_chunks.jsonl": ref_rows,
        "eval_dev.jsonl": [eval_row(r, "dev") for r in dev],
        "eval_final.jsonl": [eval_row(r, "final") for r in final],
        "excluded.jsonl": excluded,
        "spacing_changes.jsonl": spacing_log,
    }
    for name, rows in files.items():
        res.counts[name] = _write_jsonl(out_dir / name, rows)

    def stats(xs: list[int]) -> dict:
        if not xs:
            return {"n": 0}
        s = sorted(xs)
        return {"n": len(s), "sum": sum(s), "mean": round(sum(s) / len(s), 1), "p95": s[int(len(s) * 0.95) - 1], "max": s[-1]}

    valid_ref_titles = sorted({r.title for r in ref_valid})
    train_ref_titles = {r.title for r in ref_train}
    res.report = {
        "dataset_code": DATASET_CODE,
        "seed": seed,
        "dev_ratio": dev_ratio,
        "source_files": {
            "qa_train": len(qa_train), "qa_valid": len(qa_valid),
            "reference_train": len(ref_train), "reference_valid": len(ref_valid),
        },
        "outputs": dict(res.counts),
        "qa_case_tokens": stats(qa_tokens),
        "qa_case_over_embedding_limit": qa_over,
        "reference_chunk_tokens": stats(ref_tokens),
        "reference_literal_newline_files": sum(1 for r in ref_train + ref_valid if r.literal_newlines),
        "pii_masked_total": sum(r.pii_masked for r in qa_train + qa_valid + ref_train + ref_valid),
        "excluded_by_reason": dict(Counter(e["reason"] for e in excluded)),
        "spacing_fixes": {
            "changes": len(spacing_log),
            "records_changed": len({e["source_record_id"] for e in spacing_log}),
            "by_field": dict(Counter(e["field"] for e in spacing_log)),
            "by_rule": dict(Counter(e["rule"] for e in spacing_log).most_common()),
            "applied_to": "Training qa_case question/answer and reference body; eval sets kept as typed",
        },
        "eval_by_department": {
            "dev": dict(Counter(r.department for r in dev)),
            "final": dict(Counter(r.department for r in final)),
        },
        "reference_valid_not_indexed": {
            "files": len(ref_valid),
            "titles_also_in_train": sum(1 for t in valid_ref_titles if t in train_ref_titles),
            "titles_only_in_valid": [t for t in valid_ref_titles if t not in train_ref_titles],
        },
        "embedding_tokens_total": sum(qa_tokens) + sum(ref_tokens),
        "tokenizer": "cl100k_base",
    }
    (out_dir / "report.json").write_text(json.dumps(res.report, ensure_ascii=False, indent=2), encoding="utf-8")
    return res
