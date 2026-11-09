"""Prepare AI Hub dataset 59 locally: clean, chunk, build eval sets. No paid API calls.

Usage (from the project root, conda env smart_tour):
    python -m scripts.prepare_data [--version v1]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app.config import get_settings  # noqa: E402
from backend.app.ingest.prepare import DATASET_DIRNAME, prepare  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", default="v1", help="output folder name under DATA_OUTPUT_DIR/prepared")
    ap.add_argument("--seed", type=int, default=20261109)
    ap.add_argument("--dev-ratio", type=float, default=0.25)
    args = ap.parse_args()

    settings = get_settings()
    dataset_root = settings.aihub_data_dir / DATASET_DIRNAME
    if not dataset_root.is_dir():
        print(f"dataset folder not found under AIHUB_DATA_DIR: {DATASET_DIRNAME}", file=sys.stderr)
        return 2
    out_dir = Path(settings.data_output_dir) / "prepared" / args.version
    res = prepare(dataset_root, out_dir, seed=args.seed, dev_ratio=args.dev_ratio)
    print(json.dumps(res.report, ensure_ascii=False, indent=2))
    print(f"\nwritten to {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
