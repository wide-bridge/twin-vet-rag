"""Cleaning review package (docs/01 §4 진행 방법 2단계). Read-only: nothing is deleted or loaded.

    python -m scripts.clean_samples --version v2

Writes DATA_OUTPUT_DIR/cleaning/<rule version>/ (git-ignored; AI Hub excerpts stay local):
  summary.json      counts by side / rule / decision / department, characters removed, not-covered counts
  inventory.json    answer template phrase inventory (phrase key -> document count)
  candidates.jsonl  every document with a candidate: source_record_id, removed sentences (rule, decision, reason)
  review.html       representative samples (before/after), hold phrases, boundary samples, not-covered samples
"""
from __future__ import annotations

import argparse
import html
import json
import random
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app.config import get_settings  # noqa: E402
from backend.app.ingest import clean_rules as cr  # noqa: E402

SEED = 20261114


def split_case(content: str) -> tuple[str, str]:
    q, _, a = content.partition("수의사 답변:")
    return q.replace("보호자 질문:", "").strip(), a.strip()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", default="v2")
    ap.add_argument("--min-docs", type=int, default=20)
    args = ap.parse_args()
    s = get_settings()
    prepared = Path(s.data_output_dir) / "prepared" / args.version
    out_dir = Path(s.data_output_dir) / "cleaning" / cr.RULE_VERSION
    out_dir.mkdir(parents=True, exist_ok=True)

    rows = [json.loads(x) for x in (prepared / "qa_cases.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
    answers = [split_case(r["content"])[1] for r in rows]
    inventory = cr.mine_answer_inventory(answers, args.min_docs)

    results = []
    for r in rows:
        q, a = split_case(r["content"])
        rq = cr.detect(q, "question", r["department"])
        ra = cr.detect(a, "answer", r["department"], inventory)
        results.append((r, q, a, rq, ra))

    # ---- summary
    summ: dict = {"rule_version": cr.RULE_VERSION, "prepared_version": args.version, "documents": len(rows),
                  "inventory_phrases": len(inventory), "inventory_min_docs": args.min_docs}
    for side, idx in (("question", 3), ("answer", 4)):
        docs = [x for x in results if x[idx].removed]
        summ[side] = {
            "documents_with_candidates": len(docs),
            "sentences": dict(Counter(rm.decision for x in docs for rm in x[idx].removed)),
            "rules": dict(Counter(rm.rule for x in docs for rm in x[idx].removed)),
            "chars_removed_if_all": sum(len(x[1 if side == "question" else 2]) - len(x[idx].kept) for x in docs),
            "by_department": dict(Counter(x[0]["department"] for x in docs)),
            "documents_not_covered": sum(1 for x in results if x[idx].not_covered),
        }
    summ["documents_any"] = sum(1 for x in results if x[3].removed or x[4].removed)
    # held-out question sets (questions only; reference answers are not indexed)
    for subset in ("dev", "final"):
        ev = [json.loads(x) for x in (prepared / f"eval_{subset}.jsonl").read_text(encoding="utf-8").splitlines() if x]
        summ[f"eval_{subset}_questions_with_candidates"] = sum(1 for e in ev if cr.detect(e["question"], "question").removed)
        summ[f"eval_{subset}_total"] = len(ev)
    (out_dir / "summary.json").write_text(json.dumps(summ, ensure_ascii=False, indent=1), encoding="utf-8")

    df = Counter()
    for a in answers:
        keys = {cr.phrase_key(p) for snt in cr.sentences(a) for p in (cr.answer_phrases(snt) or [])}
        df.update(k for k in keys if k in inventory)
    (out_dir / "inventory.json").write_text(json.dumps(dict(df.most_common()), ensure_ascii=False, indent=0), encoding="utf-8")
    with (out_dir / "candidates.jsonl").open("w", encoding="utf-8") as f:
        for r, q, a, rq, ra in results:
            if rq.removed or ra.removed:
                f.write(json.dumps({"source_record_id": r["source_record_id"], "department": r["department"],
                                    "question_removed": [vars(x) for x in rq.removed],
                                    "answer_removed": [vars(x) for x in ra.removed]}, ensure_ascii=False) + "\n")

    write_html(out_dir / "review.html", summ, results, df)
    print(json.dumps(summ, ensure_ascii=False, indent=1))
    print("review:", out_dir / "review.html")
    return 0


# ---------------------------------------------------------------- html
def esc(t: str) -> str:
    return html.escape(t)


def marked(text: str, removed: list, kind: str) -> str:
    """Original text with the removed trailing sentences highlighted (remove = strike, hold = amber)."""
    if not removed:
        return esc(text)
    first = removed[0].text
    cut = text.rfind(first, 0, len(text) - sum(len(x.text) for x in removed[1:]) + 1)
    cut = cut if cut >= 0 else text.find(first)
    head = esc(text[:cut])
    tail = "".join(f'<span class="{x.decision}" title="{esc(x.rule)}: {esc(x.reason)}">{esc(x.text)}</span> ' for x in removed)
    return head + tail


def card(n: int, r, q, a, rq, ra, why: str) -> str:
    holds = [x for x in rq.removed + ra.removed if x.decision == "hold"]
    meta = f'{esc(r["department"])} · {esc(r["source_record_id"][:8])} · 선정 이유: {esc(why)}'
    rm_list = "".join(
        f"<li><b>{'삭제 후보' if x.decision == 'remove' else '판단 보류'}</b> [{esc(x.rule)}] {esc(x.text)} "
        f"<small>— {esc(x.reason)}</small></li>" for x in rq.removed + ra.removed)
    return f"""<section class="card" id="s{n}">
<h3>표본 {n}</h3><p class="meta">{meta}</p>
<div class="two"><div><h4>질문 (원문, 표시된 부분이 후보)</h4><p>{marked(q, rq.removed, 'q')}</p></div>
<div><h4>수의사 답변 (원문, 표시된 부분이 후보)</h4><p>{marked(a, ra.removed, 'a')}</p></div></div>
<details><summary>후보 문장 {len(rq.removed) + len(ra.removed)}개 (보류 {len(holds)}개)</summary><ul>{rm_list}</ul></details>
<details><summary>정리 후 (삭제 후보·보류 모두 뺐을 때)</summary><p><b>질문</b> {esc(rq.kept)}</p><p><b>답변</b> {esc(ra.kept)}</p></details>
</section>"""


def write_html(path: Path, summ: dict, results: list, df: Counter) -> None:
    rnd = random.Random(SEED)
    with_any = [x for x in results if x[3].removed or x[4].removed]
    by_dept = defaultdict(list)
    for x in with_any:
        by_dept[x[0]["department"]].append(x)
    picks: list[tuple] = []

    def take(pool, why, k=1):
        pool = [x for x in pool if all(x is not p[0] for p in picks)]
        for x in rnd.sample(pool, min(k, len(pool))):
            picks.append((x, why))

    for d in sorted(by_dept):  # one per department
        take(by_dept[d], f"{d} 대표")
    take([x for x in with_any if x[3].removed and not x[4].removed], "질문만 해당")
    take([x for x in with_any if x[4].removed and not x[3].removed], "답변만 해당")
    take([x for x in with_any if any(rm.decision == "hold" and "주의" in rm.reason for rm in x[4].removed)], "주의 문구 보류 포함", 2)
    take([x for x in with_any if any(rm.decision == "hold" and "주제가 겹침" in rm.reason for rm in x[4].removed)], "진료과 주제 겹침 보류")
    take(sorted(with_any, key=lambda x: -(len(x[2]) - len(x[4].kept)))[:50], "삭제량이 큰 사례")
    take([x for x in with_any if len(cr.sentences(x[4].kept)) <= 3 and x[4].removed], "남는 답변이 짧은 사례")
    cards = "\n".join(card(i + 1, *x, why) for i, (x, why) in enumerate(picks))

    # hold phrases (answer), grouped by phrase key
    hold_c, hold_ex = Counter(), {}
    for x in with_any:
        for rm in x[4].removed:
            if rm.decision == "hold":
                for p in cr.answer_phrases(rm.text) or []:
                    k = cr.phrase_key(p)
                    hold_c[(k, rm.reason)] += 1
                    hold_ex.setdefault((k, rm.reason), p.strip(" ,"))
    hold_rows = "".join(f"<tr><td>{n}</td><td>{esc(hold_ex[key])}</td><td>{esc(key[1])}</td></tr>"
                        for key, n in hold_c.most_common(40))
    remove_c = Counter()
    for x in with_any:
        for rm in x[4].removed:
            if rm.decision == "remove":
                for p in cr.answer_phrases(rm.text) or []:
                    remove_c[p.strip(" ,")] += 1
    remove_rows = "".join(f"<tr><td>{n}</td><td>{esc(p)}</td></tr>" for p, n in remove_c.most_common(30))

    # boundary: last kept sentence + first removed sentence (did the rule stop at the right place?)
    bpool = rnd.sample([x for x in with_any if x[4].removed], min(15, len(with_any)))
    bound = "".join(
        f"<tr><td>{esc(x[0]['department'])}</td><td>{esc((cr.sentences(x[4].kept) or [''])[-1])}</td>"
        f"<td class='{x[4].removed[0].decision}'>{esc(x[4].removed[0].text)}</td></tr>" for x in bpool)
    qbpool = rnd.sample([x for x in with_any if x[3].removed], min(10, len(with_any)))
    qbound = "".join(
        f"<tr><td>{esc((cr.sentences(x[3].kept) or [''])[-1])}</td><td class='remove'>{esc(x[3].removed[0].text)}</td></tr>"
        for x in qbpool)
    # not covered: template-like text outside the trailing block (left as is)
    nc = rnd.sample([x for x in results if x[3].not_covered or x[4].not_covered], 8)
    ncrows = "".join(f"<tr><td>{esc(x[0]['department'])}</td><td>{'질문' if x[3].not_covered else '답변'}</td>"
                     f"<td>{esc((x[3].not_covered or x[4].not_covered)[0])}</td></tr>" for x in nc)

    q, a = summ["question"], summ["answer"]
    doc = f"""<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>정제 후보 검토 {esc(summ['rule_version'])}</title>
<style>
:root{{--bg:#fafafa;--fg:#1d1d1f;--muted:#5f6368;--card:#fff;--line:#e3e3e3;--rm:#fde2e1;--rmfg:#9b1c1c;--hold:#fff1cc;--holdfg:#7a4b00}}
@media (prefers-color-scheme:dark){{:root{{--bg:#151515;--fg:#eee;--muted:#aaa;--card:#202020;--line:#333;--rm:#4a1f1f;--rmfg:#ffb4b0;--hold:#4a3a10;--holdfg:#ffd98a}}}}
body{{margin:0;background:var(--bg);color:var(--fg);font:15px/1.6 system-ui,-apple-system,"Malgun Gothic",sans-serif}}
main{{max-width:1180px;margin:0 auto;padding:16px}} h1{{font-size:22px}} h2{{margin-top:32px;font-size:18px}} h3{{margin:0}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 16px;margin:14px 0}}
.meta,small{{color:var(--muted)}} .two{{display:grid;grid-template-columns:1fr 1fr;gap:16px}} @media(max-width:800px){{.two{{grid-template-columns:1fr}}}}
.remove{{background:var(--rm);color:var(--rmfg);text-decoration:line-through}} .hold{{background:var(--hold);color:var(--holdfg)}}
table{{border-collapse:collapse;width:100%;font-size:14px}} td,th{{border-bottom:1px solid var(--line);padding:6px;text-align:left;vertical-align:top}}
.wrap{{overflow-x:auto}} .legend span{{padding:1px 6px;border-radius:4px;margin-right:8px}}
</style></head><body><main>
<h1>원천 정제 후보 검토 <small>{esc(summ['rule_version'])} · 원본 {esc(summ['prepared_version'])} (읽기 전용, 삭제·재색인 없음)</small></h1>
<p class="legend"><span class="remove">삭제 후보</span><span class="hold">판단 보류(유지 권장)</span> 문장에 마우스를 올리면 규칙과 이유가 보입니다.</p>
<h2>판단 기준 (사용자 검토)</h2>
<ol><li><b>삭제 대상이 맞는가</b> — 반복된다는 이유만이 아니라 그 질문·답변에서 불필요한가</li>
<li><b>의미가 보존되는가</b> — 증상·적용 조건·예외·주의사항이 지워지지 않는가</li>
<li><b>규칙을 넓게 적용해도 되는가</b> — 같은 문구가 필요한 사례가 있는가. 애매하면 유지</li></ol>
<h2>규칙 요약</h2>
<ul><li>대상은 <b>질문·답변 끝에 붙은 정형 문장 묶음</b>뿐입니다. 끝에서부터 정형 문장이 이어지는 동안만 후보로 잡고, 정형이 아닌 문장을 만나면 멈춥니다. 앞·중간에 끼어 있거나 원문 문장에 붙은 정형 문구는 건드리지 않습니다(아래 '규칙이 다루지 않는 사례').</li>
<li>질문: 정해진 관리 주제 59개(샴푸 주기, 내원 시점 등)로 시작하는 요청 문장, 또는 요청 표현만으로 된 문장.</li>
<li>답변: 문장의 모든 구절이 '정형 구절 목록'(답변 {summ['inventory_min_docs']}건 이상에서 반복된 구절 {summ['inventory_phrases']}개)에 있을 때만. 한 번만 쓰인 수의사 문장은 목록에 없으므로 걸리지 않습니다.</li>
<li>답변 후보 중 진료·처방·중단·상담 등 주의 문구가 있거나 진료과 주제와 겹치는 문장은 <b>판단 보류</b>로 따로 표시했습니다.</li></ul>
<h2>건수 (문구 탐지 건수이며 오류율이 아님)</h2>
<div class="wrap"><table><tr><th></th><th>질문</th><th>답변</th></tr>
<tr><td>후보가 있는 문서 (전체 {summ['documents']}건)</td><td>{q['documents_with_candidates']}</td><td>{a['documents_with_candidates']}</td></tr>
<tr><td>후보 문장 (삭제 후보 / 판단 보류)</td><td>{q['sentences'].get('remove', 0)} / {q['sentences'].get('hold', 0)}</td><td>{a['sentences'].get('remove', 0)} / {a['sentences'].get('hold', 0)}</td></tr>
<tr><td>모두 뺄 경우 줄어드는 글자 수</td><td>{q['chars_removed_if_all']:,}</td><td>{a['chars_removed_if_all']:,}</td></tr>
<tr><td>규칙이 다루지 않는 위치에 정형 문구가 남은 문서</td><td>{q['documents_not_covered']}</td><td>{a['documents_not_covered']}</td></tr>
<tr><td>진료과별 후보 문서</td><td>{esc(json.dumps(q['by_department'], ensure_ascii=False))}</td><td>{esc(json.dumps(a['by_department'], ensure_ascii=False))}</td></tr></table></div>
<p>참고: 평가용 질문(색인 안 됨) 중 후보가 있는 질문 — eval_dev {summ['eval_dev_questions_with_candidates']}/{summ['eval_dev_total']}, eval_final {summ['eval_final_questions_with_candidates']}/{summ['eval_final_total']}. 평가 질문 정리 여부는 별도 결정입니다.</p>
<h2>대표 표본 {len(picks)}건</h2>{cards}
<h2>판단 보류 문구 (답변, 상위 40)</h2><p>주의 문구가 있거나 진료과 주제와 겹쳐 이번 정제에서 유지를 권하는 구절입니다.</p>
<div class="wrap"><table><tr><th>건수</th><th>구절 예</th><th>보류 이유</th></tr>{hold_rows}</table></div>
<h2>삭제 후보 구절 (답변, 상위 30)</h2><div class="wrap"><table><tr><th>건수</th><th>구절</th></tr>{remove_rows}</table></div>
<h2>경계 확인 — 답변 (무작위 15건)</h2><p>남는 마지막 문장과 첫 후보 문장입니다. 원래 수의사 문장이 후보 쪽에 들어가 있으면 규칙이 넓은 것입니다.</p>
<div class="wrap"><table><tr><th>진료과</th><th>남는 마지막 문장</th><th>첫 후보 문장</th></tr>{bound}</table></div>
<h2>경계 확인 — 질문 (무작위 10건)</h2>
<div class="wrap"><table><tr><th>남는 마지막 문장</th><th>첫 후보 문장</th></tr>{qbound}</table></div>
<h2>규칙이 다루지 않는 사례 (무작위 8건, 이번에는 그대로 둠)</h2>
<div class="wrap"><table><tr><th>진료과</th><th>위치</th><th>남겨 둔 정형 문장</th></tr>{ncrows}</table></div>
</main></body></html>"""
    path.write_text(doc, encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
