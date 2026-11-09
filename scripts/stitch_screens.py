"""Generate and download Stitch screens from design/stitch/screens.json via the official CLI.

    python -m scripts.stitch_screens                 # generate missing screens, then download all
    python -m scripts.stitch_screens --only s02-answer-mobile --regenerate
    python -m scripts.stitch_screens --download-only

Output per screen: design/stitch/<key>/{prompt.md, meta.json, screen.html, screen.png}
After each download the header brand is swapped for the real logo + "Care" (scripts/apply_brand_lockup.py).
A screen whose meta.json exists is not regenerated unless --regenerate (each generation is a new Canvas screen).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "design" / "stitch" / "screens.json"
CLI = Path(os.environ["APPDATA"]) / "npm" / "node_modules" / "@google" / "stitch" / "dist" / "stitch.js"


def stitch(*args: str, timeout: int = 600) -> dict:
    proc = subprocess.run(["node", str(CLI), *args, "--json"], capture_output=True, text=True, encoding="utf-8", timeout=timeout)
    try:
        out = json.loads(proc.stdout)
    except json.JSONDecodeError:
        raise RuntimeError(f"stitch {args[0]} failed: {proc.stderr[-500:] or proc.stdout[-500:]}")
    if not out.get("success"):
        raise RuntimeError(f"stitch {args[0]} failed: {out.get('error')}")
    return out["data"]


def download(project: str, screen_id: str, folder: Path) -> None:
    for fmt, name in (("html", "screen.html"), ("png", "screen.png")):
        args = ["download", "screen", screen_id, "--project", project, "-o", str(folder / name)]
        if fmt == "png":
            args += ["--format", "png"]
        stitch(*args)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", nargs="*")
    ap.add_argument("--regenerate", action="store_true")
    ap.add_argument("--download-only", action="store_true")
    args = ap.parse_args()

    m = json.loads(MANIFEST.read_text(encoding="utf-8"))
    project = m["project_id"]
    for s in m["screens"]:
        if args.only and s["key"] not in args.only:
            continue
        folder = MANIFEST.parent / s["key"]
        folder.mkdir(parents=True, exist_ok=True)
        meta_path = folder / "meta.json"
        prompt = m["common_prefix"] + "\n\n" + s["prompt"]
        if not args.download_only and (args.regenerate or not meta_path.exists()):
            print(f"generate {s['key']} ...", flush=True)
            data = stitch("generate", "screen", "--project", project, "--device", s["device"], "--prompt", prompt)
            meta = {
                "key": s["key"], "title": s["title"], "device": s["device"], "screen_id": data["id"],
                "stitch_title": data.get("title"), "canvas_url": data.get("canvasUrl"),
                "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
                "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            }
            meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
            (folder / "prompt.md").write_text(f"# {s['title']} ({s['device']})\n\n{prompt}\n", encoding="utf-8")
        if meta_path.exists():
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            print(f"download {s['key']} ({meta['screen_id']})", flush=True)
            download(project, meta["screen_id"], folder)
            subprocess.run([sys.executable, "-m", "scripts.apply_brand_lockup", "--only", s["key"]], cwd=ROOT, check=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
