"""Comparison runs for the no-vet quality check (docs/02 §4-1, docs/03 §4, docs/04 §7).

Not a separate evaluation system: a run is one JSON file (DATA_OUTPUT_DIR/eval/runs/<run_id>.json) holding the
settings, the fixed inputs of each case and what came out. The next run of the same cases reuses those inputs
(question, dog, life stage, history), so a difference comes from the change under test (prompt or data), not from
re-deriving the dog. A saved run can be re-scored with the current checker without generating answers again.

Cases come from eval_dev only (eval_final needs an explicit final check) and must not be in the search index.
"""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import Settings
from ..db.models import Document
from ..db.models_review import ReviewItem
from ..domain.dog import LifeStage
from ..rag.embeddings import Embedder
from ..rag.llm import LLM, LLMError
from ..rag.prompts import ANSWER_SYSTEM_VARIANTS, PROMPT_VERSION, DogInfo, Turn
from . import review_dataset as ds

ROLES = ("dev", "confirm")
# USD per 1M tokens (input, output); same list as scripts/eval_answers (checked 2026-11-09)
PRICES = {"gpt-5-nano": (0.05, 0.40), "gpt-5-mini": (0.25, 2.00), "gpt-5.4-mini": (0.75, 4.50)}


class CaseError(ValueError):
    pass


def usd(usage: dict) -> float:
    total = 0.0
    for v in usage.values():
        if isinstance(v, dict) and v.get("model") in PRICES:
            pi, po = PRICES[v["model"]]
            total += v["input"] / 1e6 * pi + v["output"] / 1e6 * po
    return total


def read_rows(prepared: Path, subset: str) -> dict[str, dict]:
    with (prepared / f"eval_{subset}.jsonl").open(encoding="utf-8") as f:
        return {r["eval_id"]: r for r in map(json.loads, f) if r}


def build_inputs(db: Session, settings: Settings, spec: list[dict], rows: dict[str, dict]) -> list[dict]:
    """First run: fixed inputs per case. spec = [{"id": eval_id, "role": "dev"|"confirm"}].
    A case that already has a dataset bundle reuses the bundle's dog (what it was first answered with)."""
    cases = []
    for c in spec:
        if c.get("role") not in ROLES:
            raise CaseError(f"{c.get('id')}: role must be dev or confirm")
        row = rows.get(c["id"])
        if row is None:
            raise CaseError(f"{c['id']}: not in the allowed subset (eval_dev unless --final-check)")
        item = db.execute(select(ReviewItem).where(ReviewItem.origin == "dataset", ReviewItem.source_ref == c["id"])
                          ).scalar_one_or_none()
        if item is not None:
            dog, stage = ds.stored_dog(item, settings)
        else:
            dog, stage = ds.dog_for(row, settings)
        cases.append({"case_id": c["id"], "role": c["role"], "used_for_changes": list(c.get("used_for_changes", [])),
                      "inputs": {"question": row["question"], "source_record_id": row["source_record_id"],
                                 "department": row.get("department"), "reference_answer": row.get("reference_answer"),
                                 "dog": {"breed": dog.breed, "age_months": dog.age_months, "age_basis": dog.age_basis},
                                 "life_stage": stage.value, "history": []}})
    return cases


def assert_not_indexed(db: Session, cases: list[dict]) -> None:
    ids = [c["inputs"]["source_record_id"] for c in cases]
    found = db.execute(select(Document.source_record_id).where(Document.source_record_id.in_(ids))).scalars().all()
    if found:
        raise CaseError(f"cases already in the search index (would retrieve themselves): {sorted(found)}")


def _config(settings: Settings, embedder: Embedder, prompt: str, data_label: str | None) -> dict:
    return {"answer_model": settings.llm_model, "classifier_model": settings.llm_classifier_model,
            "embedding_model": embedder.model, "prompt_variant": prompt, "prompt_version": PROMPT_VERSION,
            "prompt_sha": hashlib.sha256(ANSWER_SYSTEM_VARIANTS[prompt].encode("utf-8")).hexdigest()[:12],
            "check_version": ds.CHECK_VERSION, "checker_model": settings.llm_model, "top_k": settings.retrieval_top_k,
            "min_score": settings.retrieval_min_score, "data_label": data_label}


def _summary(cases: list[dict]) -> dict:
    res = [c["result"] for c in cases if c.get("result")]
    return {"cases": len(cases), "response_types": dict(Counter(r["response_type"] for r in res)),
            "limited_at": dict(Counter(r["limited_at"] for r in res if r.get("limited_at"))),
            "issues": dict(Counter(t for r in res for t in (r.get("check") or {}).get("issues", []))),
            "usd": round(sum(r.get("usd", 0.0) for r in res), 4)}


def run_cases(db: Session, cases: list[dict], *, settings: Settings, llm: LLM, embedder: Embedder,
              prompt: str = "current", data_label: str | None = None, on_case=None) -> dict:
    """Answer + check every case with its fixed inputs. Returns the run record (not yet written)."""
    if prompt not in ANSWER_SYSTEM_VARIANTS:
        raise CaseError(f"unknown prompt variant {prompt}; known: {sorted(ANSWER_SYSTEM_VARIANTS)}")
    assert_not_indexed(db, cases)
    out = []
    for c in cases:
        i = c["inputs"]
        dog = DogInfo(i["dog"]["breed"], int(i["dog"]["age_months"]), age_basis=i["dog"].get("age_basis", "estimate"))
        history = [Turn(t["question"], t.get("answer")) for t in i.get("history") or []]
        row = {"question": i["question"], "department": i.get("department"), "reference_answer": i.get("reference_answer")}
        try:
            a = ds.answer_and_check(db, row, settings=settings, llm=llm, embedder=embedder, dog=dog,
                                    life_stage=LifeStage(i["life_stage"]), history=history,
                                    answer_system=ANSWER_SYSTEM_VARIANTS[prompt])
            r = a.res
            result = {"response_type": r.response_type, "decision_stage": r.decision_stage,
                      "limited_at": None if r.response_type == "grounded" else
                      {"retrieval": "retrieval", "answer": "answer"}.get(r.decision_stage, r.decision_stage),
                      "answer": r.text, "search_query": r.search_query, "index_version": r.index_version,
                      "retrieved": [{k: v for k, v in h.items() if k != "excerpt"} for h in a.retrieved],
                      "check": a.check, "usage": a.usage, "usd": round(usd(a.usage), 5)}
        except LLMError as e:
            db.rollback()
            result = {"response_type": "failed", "error": str(e), "usd": 0.0}
        out.append({**c, "result": result})
        if on_case:
            on_case(len(out), len(cases), result)
    return {"run_id": _run_id(prompt), "kind": "answer", "created_at": datetime.now(timezone.utc).isoformat(),
            "config": _config(settings, embedder, prompt, data_label), "cases": out, "summary": _summary(out)}


def rescore(run: dict, *, settings: Settings, llm: LLM) -> dict:
    """Run the current checker on the saved answers (no answer generation)."""
    out = []
    for c in run["cases"]:
        r = dict(c.get("result") or {})
        if r.get("response_type") not in (None, "failed"):
            retrieved = [{**h, "excerpt": (h.get("content") or "")[:ds.EXCERPT_CHARS]} for h in r.get("retrieved", [])]
            try:
                r["check"], u = ds.run_auto_check(
                    llm, settings, question=c["inputs"]["question"], answer=r["answer"], response_type=r["response_type"],
                    retrieved=retrieved, reference=c["inputs"].get("reference_answer"),
                    department=c["inputs"].get("department"))
                r["rescore_usage"] = u
            except LLMError as e:
                r["check"] = None
                r["rescore_error"] = str(e)
        out.append({**c, "result": r})
    rec = {**run, "run_id": run["run_id"] + "-rescored-" + ds.CHECK_VERSION, "kind": "rescore",
           "rescored_from": run["run_id"], "created_at": datetime.now(timezone.utc).isoformat(), "cases": out}
    rec["config"] = {**run["config"], "check_version": ds.CHECK_VERSION}
    rec["summary"] = _summary(out)
    return rec


def inputs_from(run: dict) -> list[dict]:
    """The fixed inputs (and roles / usage history) of an earlier run, without its results."""
    return [{k: v for k, v in c.items() if k != "result"} for c in run["cases"]]


def _run_id(prompt: str) -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + f"-{prompt}"


def write(run: dict, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    p = out_dir / f"{run['run_id']}.json"
    p.write_text(json.dumps(run, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    return p


def compare_html(a: dict, b: dict) -> str:
    """Two runs of the same cases side by side (local review file; AI Hub text stays local)."""
    import html as h

    e = h.escape
    bi = {c["case_id"]: c for c in b["cases"]}

    def unsupported(r):
        return [x["claim"] for x in ((r.get("check") or {}).get("claims") or []) if x.get("support") == "unsupported"]

    def stat(run):
        res = [c["result"] for c in run["cases"]]
        g = [r for r in res if r.get("response_type") == "grounded"]
        return {"grounded": len(g), "limited_retrieval": sum(r.get("limited_at") == "retrieval" for r in res),
                "limited_answer": sum(r.get("limited_at") == "answer" for r in res),
                "unsupported_claims": sum(len(unsupported(r)) for r in res),
                "cases_with_unsupported": sum(bool(unsupported(r)) for r in res),
                "avg_chars": round(sum(len(r.get("answer") or "") for r in g) / len(g)) if g else 0,
                "issues": dict(Counter(t for r in res for t in (r.get("check") or {}).get("issues", []))),
                "usd": run["summary"]["usd"]}

    sa, sb = stat(a), stat(b)
    rows = "".join(f"<tr><td>{e(k)}</td><td>{e(str(sa[k]))}</td><td>{e(str(sb[k]))}</td></tr>" for k in sa)

    def side(r):
        chk = r.get("check") or {}
        un = "".join(f"<li>{e(x)}</li>" for x in unsupported(r)) or "<li>없음</li>"
        miss = "".join(f"<li>{e(x)}</li>" for x in chk.get("missing_conditions") or []) or "<li>없음</li>"
        return (f"<p><b>{e(r.get('response_type', ''))}</b>{' · 제한: ' + e(r['limited_at']) if r.get('limited_at') else ''}"
                f" · 문제 유형: {e(', '.join(chk.get('issues', [])) or '없음')}</p>"
                f"<p class='ans'>{e(r.get('answer') or '')}</p>"
                f"<details><summary>근거 없는 주장 {len(unsupported(r))}</summary><ul>{un}</ul></details>"
                f"<details><summary>빠진 조건</summary><ul>{miss}</ul></details>"
                f"<details><summary>검색 근거 {len(r.get('retrieved') or [])}</summary><ol>"
                + "".join(f"<li>{e(x['title'])} ({e(x.get('department') or '-')}) {x['score']}{' · 인용' if x.get('cited') else ''}"
                          f"<br><small>{e((x.get('content') or '')[:400])}</small></li>" for x in r.get("retrieved") or [])
                + "</ol></details>")

    cards = []
    for i, c in enumerate(a["cases"], 1):
        cb = bi.get(c["case_id"])
        if cb is None:
            continue
        ra, rb = c["result"], cb["result"]
        changed = (ra.get("response_type") != rb.get("response_type")
                   or set((ra.get("check") or {}).get("issues", [])) != set((rb.get("check") or {}).get("issues", [])))
        inp = c["inputs"]
        cards.append(f"""<section class="card{' changed' if changed else ''}"><h3>{i}. [{e(c['role'])}] {e(inp.get('department') or '')}
 · 반려견 {e(str(inp['dog']['age_months']))}개월/{e(inp['life_stage'])}{' · 결과 달라짐' if changed else ''}</h3>
<p class="q">{e(inp['question'])}</p>
<div class="two"><div><h4>A: {e(a['config']['prompt_variant'])}</h4>{side(ra)}</div><div><h4>B: {e(b['config']['prompt_variant'])}</h4>{side(rb)}</div></div>
<details><summary>원천 수의사 답변 (참고 비교용)</summary><p>{e(inp.get('reference_answer') or '')}</p></details></section>""")
    return f"""<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>비교 {e(a['run_id'])} vs {e(b['run_id'])}</title><style>
:root{{--bg:#fafafa;--fg:#1d1d1f;--card:#fff;--line:#e3e3e3;--muted:#5f6368;--hi:#fff6d6}}
@media (prefers-color-scheme:dark){{:root{{--bg:#151515;--fg:#eee;--card:#202020;--line:#333;--muted:#aaa;--hi:#3a3216}}}}
body{{margin:0;background:var(--bg);color:var(--fg);font:15px/1.6 system-ui,"Malgun Gothic",sans-serif}} main{{max-width:1200px;margin:0 auto;padding:16px}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 16px;margin:14px 0}} .changed{{border-left:5px solid #d99a00}}
.two{{display:grid;grid-template-columns:1fr 1fr;gap:16px}} @media(max-width:800px){{.two{{grid-template-columns:1fr}}}}
.q{{background:var(--hi);padding:8px;border-radius:6px}} .ans{{white-space:pre-wrap}} small{{color:var(--muted)}}
table{{border-collapse:collapse}} td,th{{border-bottom:1px solid var(--line);padding:4px 10px;text-align:left}}
</style></head><body><main><h1>답변 비교 (같은 입력, 프롬프트만 다름)</h1>
<p>A = {e(a['run_id'])} · B = {e(b['run_id'])} · 점검기 {e(a['config']['check_version'])} / {e(b['config']['check_version'])}.
자동 점검은 운영자 확인의 보조이며 안전성·수의학적 검증이 아닙니다. 15건은 전체 성능 입증이 아니라 발견한 오류가 줄었는지·뚜렷한 부작용이 없는지 보는 규모입니다.</p>
<table><tr><th></th><th>A</th><th>B</th></tr>{rows}</table>{''.join(cards)}</main></body></html>"""
