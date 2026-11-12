"""Public animal-hospital data (행정안전부_동물_동물병원 조회서비스, data.go.kr 15154952) -> clinic table.

Operator tool (P06 style). The whole list (~10.7k rows, 100 per page) is fetched and upserted by public_id; Kakao
place ids and partner flags already on a row are kept. Mobile numbers in TELNO are dropped (a 2021 incident in
another public dataset exposed hospital directors' mobiles; docs/03 §6).
"""
from __future__ import annotations

import time
import uuid
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session as DB

from ..db.models import Clinic
from ..services.clinics import is_mobile, name_key, phone_ok, road_key

URL = "https://apis.data.go.kr/1741000/animal_hospitals/info"  # the portal lists the base URL; the operation is /info
KST = timezone(timedelta(hours=9))
STATUS = {"01": "open", "02": "paused", "03": "closed"}


def fetch_page(key: str, page: int, rows: int = 100, timeout: float = 30.0) -> tuple[list[dict], int]:
    for attempt in range(3):
        try:
            r = httpx.get(URL, params={"serviceKey": key, "pageNo": page, "numOfRows": rows, "returnType": "json"},
                          timeout=timeout)
            r.raise_for_status()
            body = r.json()["response"]["body"]
            items = body.get("items") or {}
            item = items.get("item") if isinstance(items, dict) else items
            return (item if isinstance(item, list) else [item] if item else []), int(body.get("totalCount") or 0)
        except Exception:
            if attempt == 2:
                raise
            time.sleep(2 * (attempt + 1))
    return [], 0


def _float(v) -> float | None:
    try:
        return float(v) if v not in (None, "") else None
    except ValueError:
        return None


def _ts(v) -> datetime | None:
    try:
        return datetime.strptime(v, "%Y-%m-%d %H:%M:%S").replace(tzinfo=KST) if v else None
    except ValueError:
        return None


def to_row(it: dict) -> dict | None:
    name = (it.get("BPLC_NM") or "").strip()
    mng, grp = (it.get("MNG_NO") or "").strip(), (it.get("OPN_ATMY_GRP_CD") or "").strip()
    if not name or not mng:
        return None
    phone = (it.get("TELNO") or "").strip()
    phone = phone if phone_ok(phone) and not is_mobile(phone) else None
    road = (it.get("ROAD_NM_ADDR") or "").strip() or None
    return {"source": "public", "public_id": f"{grp}-{mng}"[:40], "name": name[:100], "name_key": name_key(name),
            "road_address": road, "road_key": road_key(road), "lot_address": (it.get("LOTNO_ADDR") or "").strip() or None,
            "public_phone": phone, "crd_x": _float(it.get("CRD_INFO_X")), "crd_y": _float(it.get("CRD_INFO_Y")),
            "business_status": STATUS.get((it.get("SALS_STTS_CD") or "").strip(), "other"),
            "public_updated_at": _ts(it.get("DAT_UPDT_PNT"))}


def upsert(db: DB, rows: list[dict]) -> int:
    if not rows:
        return 0
    by_id = {r["public_id"]: r for r in rows}  # one row per id within a statement
    values = [{"id": uuid.uuid4(), **r} for r in by_id.values()]
    stmt = pg_insert(Clinic).values(values)
    cols = [c for c in values[0] if c not in ("id", "source", "public_id")]
    stmt = stmt.on_conflict_do_update(constraint="uq_clinic_public_id",
                                      set_={**{c: stmt.excluded[c] for c in cols}, "updated_at": func.now()})
    db.execute(stmt)
    db.commit()
    return len(values)


def load_all(db: DB, key: str, rows_per_page: int = 100, pause: float = 0.1, log=print) -> dict:
    first, total = fetch_page(key, 1, rows_per_page)
    pages = (total + rows_per_page - 1) // rows_per_page
    done = upsert(db, [r for r in map(to_row, first) if r])
    for p in range(2, pages + 1):
        items, _ = fetch_page(key, p, rows_per_page)
        done += upsert(db, [r for r in map(to_row, items) if r])
        if p % 20 == 0:
            log(f"  page {p}/{pages} ({done} rows)")
        time.sleep(pause)
    return {"total_reported": total, "upserted": done, "pages": pages}
