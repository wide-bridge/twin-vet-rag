"""Load prepared knowledge into PostgreSQL + pgvector.

    python -m scripts.load_knowledge --version v1 --embedder fake            # offline, no cost (non-semantic vectors)
    python -m scripts.load_knowledge --version v1 --embedder openai          # prints token/cost estimate, asks to confirm
    python -m scripts.load_knowledge --version v1 --embedder openai --yes --max-usd 1.0
    python -m scripts.load_knowledge --deactivate <source_record_id> ...     # exclude from search immediately
    python -m scripts.load_knowledge --version v3 --embedder openai --content-only   # source text cleaning: refuse
                                                    # new documents or metadata changes before anything is written
    python -m scripts.load_knowledge --rollback-run <ingestion_run id>     # all-or-nothing return to previous versions

Fake and OpenAI vectors never mix: search filters on chunk.embedding_model.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app.config import get_settings  # noqa: E402
from backend.app.db.engine import session_factory  # noqa: E402
from backend.app.ingest import load  # noqa: E402
from backend.app.rag.embeddings import FakeEmbedder, OpenAIEmbedder  # noqa: E402

USD_PER_1M_TOKENS = {"text-embedding-3-small": 0.02}  # OpenAI price list checked 2026-11-09; re-check before paid runs


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", default="v1")
    ap.add_argument("--embedder", choices=["fake", "openai"], default="fake")
    ap.add_argument("--limit", type=int, help="load only N documents (sampling)")
    ap.add_argument("--yes", action="store_true", help="skip the paid-run confirmation prompt")
    ap.add_argument("--max-usd", type=float, default=1.0, help="refuse a paid run whose estimate exceeds this")
    ap.add_argument("--no-activate", action="store_true")
    ap.add_argument("--deactivate", nargs="+", metavar="SOURCE_RECORD_ID")
    ap.add_argument("--dataset-code", default="aihub59")
    ap.add_argument("--content-only", action="store_true", help="cleaning path: body text changes only")
    ap.add_argument("--rollback-run", metavar="RUN_ID")
    args = ap.parse_args()

    settings = get_settings()
    Session = session_factory()

    if args.rollback_run:
        import uuid

        with Session() as db:
            out = load.rollback(db, uuid.UUID(args.rollback_run))
        print(json.dumps(out, ensure_ascii=False, indent=1))
        return 0 if out["ok"] else 5

    if args.deactivate:
        with Session() as db:
            n = load.set_document_status(db, args.dataset_code, args.deactivate, "inactive")
        print(f"deactivated {n} document(s)")
        return 0

    if args.embedder == "openai":
        if not settings.openai_api_key:
            print("OPENAI_API_KEY is not set (ENV_FILE or environment)", file=sys.stderr)
            return 2
        embedder = OpenAIEmbedder(settings.openai_api_key.get_secret_value(), settings.embedding_model)
    else:
        embedder = FakeEmbedder()

    prepared_dir = Path(settings.data_output_dir) / "prepared" / args.version
    dataset_code, docs = load.read_prepared(prepared_dir, limit=args.limit)

    with Session() as db:
        run = load.start_run(db, dataset_code, args.version, embedder.model)
        try:
            try:
                print("stage:", json.dumps(load.stage(db, run, docs, content_only=args.content_only), ensure_ascii=False))
            except load.ContentOnlyViolation as e:
                print(f"refused (content-only): {e}", file=sys.stderr)
                for prob in e.problems[:20]:
                    print("  ", json.dumps(prob, ensure_ascii=False), file=sys.stderr)
                load.fail_run(db, run, "content_only_violation")
                return 6
            pending = load.pending_chunk_stats(db, run)
            usd = pending["tokens"] / 1_000_000 * USD_PER_1M_TOKENS.get(embedder.model, 0.0)
            print(f"to embed: {pending['chunks']} chunks, ~{pending['tokens']:,} tokens, est. ${usd:.4f} ({embedder.model})")
            if args.embedder == "openai":
                if usd > args.max_usd:
                    print(f"estimate exceeds --max-usd {args.max_usd}; aborting before any API call", file=sys.stderr)
                    load.fail_run(db, run, "budget_exceeded")
                    return 3
                if not args.yes and input("proceed with paid embedding? [y/N] ").strip().lower() != "y":
                    load.fail_run(db, run, "cancelled")
                    return 1
            print("embed:", load.embed_pending(db, run, embedder, progress=lambda n: print(f"  embedded {n}", end="\r")))
            if getattr(embedder, "usage_tokens", None):
                print(f"actual usage: {embedder.usage_tokens:,} tokens")
            report = load.validate(db, run, embedder.model)
            print("validate:", report)
            if not report["ok"]:
                load.fail_run(db, run, "validation_failed")
                return 4
            if not args.no_activate:
                print("activate:", load.activate(db, run))
                if args.content_only:
                    print(f"ingestion run: {run.id}  (undo: --rollback-run {run.id})")
                else:
                    print(f"ingestion run: {run.id}  (not a --content-only load: --rollback-run will refuse it)")
        except Exception as e:  # keep the active index untouched
            load.fail_run(db, run, type(e).__name__)
            raise
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
