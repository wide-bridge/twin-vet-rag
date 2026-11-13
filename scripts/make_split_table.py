"""Write DATA_OUTPUT_DIR/prepared/<version>/split_table.jsonl (fixed seed, duplicate groups kept in one fold).

    python -m scripts.make_split_table --version v2 [--folds 5] [--seed 20261113]

Local files only (no network, no DB). See backend/app/ingest/split.py.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app.config import get_settings  # noqa: E402
from backend.app.ingest.split import DEFAULT_FOLDS, DEFAULT_SEED, write_split_table  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", default="v2")
    ap.add_argument("--folds", type=int, default=DEFAULT_FOLDS)
    ap.add_argument("--seed", type=int, default=DEFAULT_SEED)
    a = ap.parse_args()
    prepared = get_settings().data_output_dir / "prepared" / a.version
    if not (prepared / "qa_cases.jsonl").exists():
        print("prepared data not found:", prepared)
        return 2
    print(json.dumps(write_split_table(prepared, a.folds, a.seed), ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
