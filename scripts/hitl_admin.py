"""MVP1 HITL operator commands (docs/04 §7). No public web API exists for these.

    python -m scripts.hitl_admin seed-ontology
    python -m scripts.hitl_admin create-reviewer --role operator --name "운영자"   # prints the access key ONCE
    python -m scripts.hitl_admin deactivate-reviewer --id <uuid>
    python -m scripts.hitl_admin mock-items                     # stage-A mock bundles (+ AI suggestions)
    python -m scripts.hitl_admin dataset-items --n 5 [--subset dev] [--prelabel] [--hide-pct 30] [--max-usd 1] --yes
                                       # real bundles: held-out questions answered by the app pipeline + automatic check (paid)
    python -m scripts.hitl_admin recheck --item <uuid> --yes  # same question again after a fix (paid)
    python -m scripts.hitl_admin issues                         # automatic check issue counts (free)
    python -m scripts.hitl_admin evaluate --candidate <uuid> --embedder {fake|openai} [--version v2 --fold 0 --n 50]
    python -m scripts.hitl_admin activate --candidate <uuid> --embedder {fake|openai} --yes
    python -m scripts.hitl_admin status

Target DB = DATABASE_URL (local by default). Any non-local DB is treated as production: operators cannot approve,
and mock knowledge cannot be activated. The OpenAI embedder costs a little (one candidate + N questions).
"""
from __future__ import annotations

import argparse
import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402

from backend.app.config import get_settings  # noqa: E402
from backend.app.db.engine import session_factory  # noqa: E402
from backend.app.db.models_review import KnowledgeCandidate, Reviewer  # noqa: E402
from backend.app.ingest.split import qa_question  # noqa: E402
from backend.app.rag.embeddings import FakeEmbedder, OpenAIEmbedder  # noqa: E402
from backend.app.rag.llm import OpenAILLM  # noqa: E402
from backend.app.services import review as svc  # noqa: E402
from backend.app.services import review_dataset as ds  # noqa: E402

# USD per 1M tokens (input, output); same list as scripts/eval_answers (checked 2026-11-09)
PRICES = {"gpt-5-nano": (0.05, 0.40), "gpt-5-mini": (0.25, 2.00), "gpt-5.4-mini": (0.75, 4.50)}


def _usd(usages: list[dict]) -> float:
    total = 0.0
    for u in usages:
        for v in u.values():
            if isinstance(v, dict) and v.get("model") in PRICES:
                pi, po = PRICES[v["model"]]
                total += v["input"] / 1e6 * pi + v["output"] / 1e6 * po
    return total


def _embedder(kind: str):
    s = get_settings()
    if kind == "fake":
        return FakeEmbedder()
    if not s.openai_api_key:
        raise SystemExit("OPENAI_API_KEY is not set")
    return OpenAIEmbedder(s.openai_api_key.get_secret_value(), s.embedding_model)


def regression_questions(version: str, fold: int, n: int) -> list[str]:
    """Questions of one split-table fold (dev/regression only — never called an independent evaluation)."""
    prepared = get_settings().data_output_dir / "prepared" / version
    table = prepared / "split_table.jsonl"
    if not table.exists():
        raise SystemExit(f"split table missing: run python -m scripts.make_split_table --version {version}")
    ids = []
    with table.open(encoding="utf-8") as f:
        for line in f:
            r = json.loads(line)
            if r["fold"] == fold:
                ids.append(r["source_record_id"])
    wanted = set(sorted(ids)[: n * 4])
    out: list[str] = []
    # eval_final is reserved for the final confirmation (docs/02 §4-1); never used by routine checks
    for name, field in (("qa_cases.jsonl", "content"), ("eval_dev.jsonl", "question")):
        p = prepared / name
        if not p.exists():
            continue
        with p.open(encoding="utf-8") as f:
            for line in f:
                r = json.loads(line)
                if r["source_record_id"] in wanted:
                    out.append(qa_question(r[field]) if field == "content" else r[field])
    return sorted(out)[:n]


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("seed-ontology")
    p = sub.add_parser("create-reviewer")
    p.add_argument("--role", choices=["vet", "operator"], required=True)
    p.add_argument("--name", required=True)
    p.add_argument("--email")
    p = sub.add_parser("deactivate-reviewer")
    p.add_argument("--id", required=True)
    sub.add_parser("mock-items")
    p = sub.add_parser("dataset-items")
    p.add_argument("--n", type=int, default=20)
    p.add_argument("--subset", choices=["dev"], default="dev")  # eval_final: final confirmation only
    p.add_argument("--version", default="v2")
    p.add_argument("--prelabel", action="store_true", help="also fill the vet label form (vet stage)")
    p.add_argument("--hide-pct", type=int, default=30)
    p.add_argument("--max-usd", type=float, default=1.0, help="stop before the next bundle once spending reaches this")
    p.add_argument("--yes", action="store_true")
    for name in ("evaluate", "activate"):
        p = sub.add_parser(name)
        p.add_argument("--candidate", required=True)
        p.add_argument("--embedder", choices=["fake", "openai"], required=True)
        if name == "evaluate":
            p.add_argument("--version", default="v2")
            p.add_argument("--fold", type=int, default=0)
            p.add_argument("--n", type=int, default=50)
        else:
            p.add_argument("--yes", action="store_true")
    p = sub.add_parser("recheck")
    p.add_argument("--item", required=True)
    p.add_argument("--yes", action="store_true")
    sub.add_parser("issues")
    sub.add_parser("status")
    a = ap.parse_args()

    s = get_settings()
    print("target host:", make_url(s.database_url).host, "| production rules:", s.hitl_production)
    with session_factory(s.database_url)() as db:
        if a.cmd == "seed-ontology":
            print(svc.seed_ontology(db))
        elif a.cmd == "create-reviewer":
            r, key = svc.create_reviewer(db, a.role, a.name, a.email)
            print(f"reviewer {r.id} ({r.role}) created. Access key (shown once, store it safely):\n{key}")
        elif a.cmd == "deactivate-reviewer":
            r = db.get(Reviewer, uuid.UUID(a.id))
            if r is None:
                print("not found")
                return 1
            r.active = False
            db.commit()
            print("deactivated", r.id)
        elif a.cmd == "mock-items":
            print(svc.seed_ontology(db))
            print(svc.create_mock_items(db))
        elif a.cmd == "dataset-items":
            if not a.yes:
                print(f"add --yes to run (paid: about ${a.n * (0.012 if a.prelabel else 0.008):.2f} for {a.n} bundles)")
                return 2
            if not s.openai_api_key:
                raise SystemExit("OPENAI_API_KEY is not set")
            key = s.openai_api_key.get_secret_value()
            print(svc.seed_ontology(db))
            rows = ds.load_rows(s.data_output_dir / "prepared" / a.version / f"eval_{a.subset}.jsonl")
            for r in rows:
                r.setdefault("subset", a.subset)
            rep = ds.build_dataset_items(
                db, rows, settings=s, llm=OpenAILLM(key, timeout=s.llm_timeout_seconds),
                embedder=OpenAIEmbedder(key, s.embedding_model), n=a.n, prelabel=a.prelabel,
                hide_pct=a.hide_pct, on_progress=lambda rep, t: print(f"[{rep.created}/{a.n}] {t}", flush=True),
                should_stop=lambda rep: _usd(rep.usage) >= a.max_usd)
            print(json.dumps({**rep.as_dict(), "usd": round(_usd(rep.usage), 4)}, ensure_ascii=False))
        elif a.cmd == "recheck":
            item = db.get(svc.ReviewItem, uuid.UUID(a.item))
            if item is None:
                print("item not found")
                return 1
            if not a.yes:
                print("add --yes to run (paid: about $0.008)")
                return 2
            key = s.openai_api_key.get_secret_value()
            out = ds.recheck_item(db, item, settings=s, llm=OpenAILLM(key, timeout=s.llm_timeout_seconds),
                                  embedder=OpenAIEmbedder(key, s.embedding_model))
            print(json.dumps({"issues_before": out["issues_before"], "issues_after": out["issues_after"],
                              "response_type": out["response_type"], "usd": round(_usd([out["usage"]]), 4)},
                             ensure_ascii=False))
        elif a.cmd == "issues":
            from collections import Counter
            from backend.app.db.models_review import ReviewEvent
            evs = db.execute(select(ReviewEvent).where(ReviewEvent.event == "auto_checked")).scalars().all()
            latest = {}
            for e in sorted(evs, key=lambda e: e.created_at):
                latest[e.item_id] = e.detail
            c = Counter(t for d in latest.values() for t in d.get("issues", []))
            print(json.dumps({"checked_items": len(latest), "with_issues": sum(bool(d.get("issues")) for d in latest.values()),
                              "issues": {f"{k} ({ds.ISSUE_TYPES[k]})": v for k, v in c.most_common()}}, ensure_ascii=False))
        elif a.cmd in ("evaluate", "activate"):
            cand = db.get(KnowledgeCandidate, uuid.UUID(a.candidate))
            if cand is None:
                print("candidate not found")
                return 1
            emb = _embedder(a.embedder)
            if a.cmd == "evaluate":
                qs = regression_questions(a.version, a.fold, a.n)
                print(svc.evaluate_candidate(db, cand, emb, qs, top_k=s.retrieval_top_k, max_spillover=s.hitl_max_spillover,
                                             min_questions=s.hitl_min_eval_questions))
            else:
                if not a.yes:
                    print("add --yes to activate (this changes what RAG search returns)")
                    return 2
                try:
                    print(svc.activate_candidate(db, cand, emb, production=s.hitl_production))
                except svc.ReviewError as e:
                    print("refused:", e.message)
                    return 1
        elif a.cmd == "status":
            print(json.dumps(svc.counts(db), ensure_ascii=False))
            ops = db.execute(select(Reviewer.role, Reviewer.display_name, Reviewer.active)).all()
            print([tuple(o) for o in ops])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
