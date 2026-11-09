"""Retrieval check on the fixed evaluation set (no LLM calls; query embeddings only, ~$0.001 per 100).

    python -m scripts.eval_retrieval --subset dev --n 100            # dev sample, report only
    python -m scripts.eval_retrieval --subset dev --n 600 --out data_output/eval/retrieval_dev.json

Metrics (proxies — the eval set has no gold chunk ids):
  dept@1 / dept@k  : the question's department appears at rank 1 / within top k
  disease@k        : for questions with a disease label, a hit with the same disease label within top k
  top1 score       : distribution, used to set RETRIEVAL_MIN_SCORE (docs/04 §4 step 3)
Do not tune on the final subset (docs/03 §5).
"""
from __future__ import annotations

import argparse
import json
import random
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app.config import get_settings  # noqa: E402
from backend.app.db.engine import session_factory  # noqa: E402
from backend.app.domain.dog import LifeStage  # noqa: E402
from backend.app.rag.embeddings import OpenAIEmbedder  # noqa: E402
from backend.app.rag.retrieval import search  # noqa: E402

# Off-topic probes: the top-1 score of these shows where "nothing relevant" sits.
OFF_TOPIC = [
    "고양이가 사료를 안 먹고 토해요", "햄스터 케이지는 어떤 걸 사야 하나요?", "주식 투자 어떻게 시작하나요?",
    "오늘 서울 날씨 어때요?", "파이썬 리스트 정렬 방법 알려줘", "앵무새가 깃털을 뽑아요",
    "김치찌개 맛있게 끓이는 법", "자동차 엔진오일 교체 주기", "토끼가 설사를 해요", "아이폰 배터리가 빨리 닳아요",
]


def pct(xs: list[float], p: float) -> float:
    s = sorted(xs)
    return round(s[min(len(s) - 1, int(len(s) * p))], 3)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--subset", choices=["dev", "final"], default="dev")
    ap.add_argument("--n", type=int, default=100)
    ap.add_argument("--k", type=int, default=6)
    ap.add_argument("--version", default="v2")
    ap.add_argument("--out")
    args = ap.parse_args()

    s = get_settings()
    rows = [json.loads(x) for x in (Path(s.data_output_dir) / "prepared" / args.version / f"eval_{args.subset}.jsonl")
            .read_text(encoding="utf-8").splitlines()]
    random.Random(7).shuffle(rows)
    rows = rows[: args.n]
    emb = OpenAIEmbedder(s.openai_api_key.get_secret_value(), s.embedding_model)
    vecs = emb.embed([r["question"] for r in rows] + OFF_TOPIC)
    Session = session_factory()
    top1, d1, dk, dis_n, dis_k, off = [], 0, 0, 0, 0, []
    with Session() as db:
        for r, v in zip(rows, vecs):
            stage = LifeStage(r["life_stages"][0]) if r["life_stages"] else None
            with db.begin():
                hits = search(db, v, model=emb.model, life_stage=stage, top_k=args.k)
            if not hits:
                continue
            top1.append(hits[0].score)
            d1 += hits[0].department == r["department"]
            dk += any(h.department == r["department"] for h in hits)
            if r["disease_names"]:
                dis_n += 1
                dis_k += any(set(h.disease_names) & set(r["disease_names"]) for h in hits)
        for v in vecs[len(rows):]:
            with db.begin():
                hits = search(db, v, model=emb.model, life_stage=None, top_k=1)
            off.append(hits[0].score if hits else 0.0)
    n = len(top1)
    report = {
        "subset": args.subset, "n": n, "k": args.k,
        "dept@1": round(d1 / n, 3), f"dept@{args.k}": round(dk / n, 3),
        f"disease@{args.k}": round(dis_k / dis_n, 3) if dis_n else None, "disease_labelled": dis_n,
        "top1_score": {"p05": pct(top1, .05), "p25": pct(top1, .25), "median": round(statistics.median(top1), 3), "p95": pct(top1, .95)},
        "off_topic_top1": {"max": round(max(off), 3), "median": round(statistics.median(off), 3), "values": [round(x, 3) for x in off]},
        "embedding_tokens": emb.usage_tokens,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
