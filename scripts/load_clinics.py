"""Load/refresh the public animal-hospital list (행정안전부_동물_동물병원 조회서비스) into the clinic table.

    python -m scripts.load_clinics            # DATABASE_URL from settings (local by default)

Needs DATA_GO_KR_SERVICE_KEY (the portal's Decoding key) in ENV_FILE. Upserts by public id, keeps Kakao place
ids and partner flags. Re-run to pick up openings, closures and address/phone changes (the source updates daily).
Prints counts only.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402

from backend.app.config import get_settings  # noqa: E402
from backend.app.db.engine import session_factory  # noqa: E402
from backend.app.ingest.clinics import load_all  # noqa: E402


def main() -> int:
    s = get_settings()
    if not s.data_go_kr_service_key:
        print("DATA_GO_KR_SERVICE_KEY is not set")
        return 2
    print("target host:", make_url(s.database_url).host)
    with session_factory(s.database_url)() as db:
        r = load_all(db, s.data_go_kr_service_key.get_secret_value())
        by = dict(db.execute(text("SELECT business_status, count(*) FROM clinic WHERE source='public' GROUP BY 1")).all())
    print(r, by)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
