"""M03 vet accounts (docs/04 §7-1). Separate from HITL reviewers (scripts.hitl_admin).

    python -m scripts.vet_admin create-vet --name "김수의" --clinic "튼튼동물병원" [--intro "..."] [--test]
    python -m scripts.vet_admin deactivate-vet --id <uuid>
    python -m scripts.vet_admin list

Target DB = DATABASE_URL (local by default). The access key is printed ONCE (only its hash is stored): keep it out of
chats, tickets and commits. --test marks a test account: it is never listed to guardians in production.
"""
from __future__ import annotations

import argparse
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout.reconfigure(encoding="utf-8")

from sqlalchemy import select  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402

from backend.app.config import get_settings  # noqa: E402
from backend.app.db.engine import session_factory  # noqa: E402
from backend.app.db.models_m03 import VetAccount, VetSession  # noqa: E402
from backend.app.services import m03  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("create-vet")
    p.add_argument("--name", required=True)
    p.add_argument("--clinic")
    p.add_argument("--intro")
    p.add_argument("--test", action="store_true", help="test account (hidden from guardians in production)")
    p = sub.add_parser("deactivate-vet")
    p.add_argument("--id", required=True)
    sub.add_parser("list")
    a = ap.parse_args()

    s = get_settings()
    print("target host:", make_url(s.database_url).host, "| M03 enabled:", s.m03_enabled)
    with session_factory(s.database_url)() as db:
        if a.cmd == "create-vet":
            v, key = m03.create_vet(db, a.name, a.clinic, a.intro, is_test=a.test)
            print(f"vet {v.id} created{' (test)' if v.is_test else ''}. Access key (shown once, store it safely):\n{key}")
        elif a.cmd == "deactivate-vet":
            v = db.get(VetAccount, uuid.UUID(a.id))
            if v is None:
                print("not found")
                return 1
            v.active = False
            db.query(VetSession).filter(VetSession.vet_id == v.id).delete()
            db.commit()
            print("deactivated", v.id)
        else:
            for v in db.execute(select(VetAccount).order_by(VetAccount.created_at)).scalars():
                print(v.id, v.display_name, v.clinic_name or "-", "test" if v.is_test else "real",
                      "active" if v.active else "off")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
