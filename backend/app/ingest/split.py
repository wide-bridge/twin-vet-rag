"""Split table for later model development (docs/01 §7 혼합안).

All current data is used for knowledge and labeling now; this table only fixes, in advance, which fold each
record would belong to if a model is trained/evaluated later. Evaluations on it are dev/regression only.

- One row per QA record (Training qa_cases + Validation eval_dev/eval_final).
- dup_group = exact-duplicate key of the question (whitespace/punctuation ignored); a group never spans folds.
- Folds are assigned per department so each fold keeps roughly the same department mix (stratified, grouped).
- Deterministic: same input + seed -> same table.
"""
from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from pathlib import Path
from typing import Iterable

from .prepare import dedup_key

DEFAULT_SEED = 20261113
DEFAULT_FOLDS = 5


def qa_question(content: str) -> str:
    """Question part of a prepared qa_case ('보호자 질문: ...\\n수의사 답변: ...')."""
    head = content.split("\n수의사 답변:", 1)[0]
    return head.removeprefix("보호자 질문:").strip()


def _read_jsonl(path: Path) -> Iterable[dict]:
    with path.open(encoding="utf-8") as f:
        for line in f:
            if line.strip():
                yield json.loads(line)


def collect_records(prepared_dir: Path) -> list[dict]:
    rows: list[dict] = []
    for r in _read_jsonl(prepared_dir / "qa_cases.jsonl"):
        rows.append({"source_record_id": r["source_record_id"], "split": "train", "department": r.get("department"),
                     "question": qa_question(r["content"])})
    for name in ("eval_dev.jsonl", "eval_final.jsonl"):
        p = prepared_dir / name
        if p.exists():
            for r in _read_jsonl(p):
                rows.append({"source_record_id": r["source_record_id"], "split": "valid",
                             "department": r.get("department"), "question": r["question"]})
    return rows


def _order(seed: int, key: str) -> str:
    return hashlib.sha256(f"{seed}:{key}".encode()).hexdigest()


def build_split_table(records: list[dict], folds: int = DEFAULT_FOLDS, seed: int = DEFAULT_SEED) -> list[dict]:
    groups: dict[str, list[dict]] = defaultdict(list)
    for r in records:
        groups[dedup_key(r["question"])[:16]].append(r)
    # a group's department = its most common department (ties: alphabetical)
    by_dept: dict[str, list[str]] = defaultdict(list)
    for g, members in groups.items():
        counts: dict[str, int] = defaultdict(int)
        for m in members:
            counts[m["department"] or "미상"] += 1
        dept = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
        by_dept[dept].append(g)
    fold_of: dict[str, int] = {}
    for dept in sorted(by_dept):
        for i, g in enumerate(sorted(by_dept[dept], key=lambda g: _order(seed, g))):
            fold_of[g] = i % folds
    out = []
    for g, members in groups.items():
        for m in members:
            out.append({"source_record_id": m["source_record_id"], "split": m["split"], "department": m["department"],
                        "dup_group": g, "fold": fold_of[g]})
    out.sort(key=lambda r: (r["split"], r["source_record_id"]))
    return out


def write_split_table(prepared_dir: Path, folds: int = DEFAULT_FOLDS, seed: int = DEFAULT_SEED) -> dict:
    table = build_split_table(collect_records(prepared_dir), folds, seed)
    path = prepared_dir / "split_table.jsonl"
    with path.open("w", encoding="utf-8", newline="\n") as f:
        for r in table:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    per_fold: dict[int, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for r in table:
        per_fold[r["fold"]][r["department"] or "미상"] += 1
    groups = {r["dup_group"] for r in table}
    return {"path": str(path), "rows": len(table), "dup_groups": len(groups), "duplicates": len(table) - len(groups),
            "folds": folds, "seed": seed, "per_fold": {k: dict(v) for k, v in sorted(per_fold.items())}}
