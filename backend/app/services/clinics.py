"""주치동물병원: Kakao Local search (live, never stored) and matching to the public-data clinic master.

Kakao policy (docs/03 §6, devtalk answers 2026-08-26 / 2026-09-16): search results may be shown in real time and
used once as a lookup key; only the place id and place URL may be stored. So this module returns Kakao results to
the browser without saving or logging them, and `match` reads them only to find our own public-data record.
"""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from typing import Protocol

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session as DB

from ..api.errors import ApiError
from ..db.models import Clinic

KAKAO_URL = "https://dapi.kakao.com/v2/local/search/keyword.json"
PLACE_URL = re.compile(r"^https?://place\.map\.kakao\.com/\d{1,20}$")

# Kakao writes provinces short ("서울 마포구"), public data long ("서울특별시 마포구"); compare on the short form.
SIDO = {
    "서울특별시": "서울", "부산광역시": "부산", "대구광역시": "대구", "인천광역시": "인천", "광주광역시": "광주",
    "대전광역시": "대전", "울산광역시": "울산", "세종특별자치시": "세종", "경기도": "경기", "강원특별자치도": "강원",
    "강원도": "강원", "충청북도": "충북", "충청남도": "충남", "전북특별자치도": "전북", "전라북도": "전북",
    "전라남도": "전남", "경상북도": "경북", "경상남도": "경남", "제주특별자치도": "제주",
}


def name_key(name: str) -> str:
    """Name for comparison: no spaces/punctuation, lower case."""
    return re.sub(r"[^0-9a-z가-힣]", "", (name or "").lower())[:100]


def road_key(addr: str | None) -> str | None:
    """Road address up to the building number: '서울특별시 마포구 망원로 12, 2층 (망원동)' -> '서울마포구망원로12'."""
    if not addr:
        return None
    a = addr.split(",")[0].split("(")[0].strip()
    parts = a.split()
    if not parts:
        return None
    parts[0] = SIDO.get(parts[0], parts[0])
    return "".join(parts)[:200] or None


def phone_ok(p: str | None) -> bool:
    return bool(p) and re.fullmatch(r"[0-9-]{7,20}", p) is not None and sum(ch.isdigit() for ch in p) >= 7


def is_mobile(p: str | None) -> bool:
    return bool(p) and re.sub(r"\D", "", p).startswith(("010", "011", "016", "017", "018", "019"))


# ------------------------------------------------------------------ Kakao Local (live only)
@dataclass
class Place:
    place_id: str
    name: str
    road_address: str
    address: str
    phone: str
    distance_m: int | None
    place_url: str

    def view(self) -> dict:
        return {"place_id": self.place_id, "name": self.name, "road_address": self.road_address,
                "address": self.address, "phone": self.phone, "distance_m": self.distance_m, "place_url": self.place_url}


class PlaceSearch(Protocol):
    def search(self, query: str, x: float | None, y: float | None, radius: int | None) -> list[Place]: ...


class KakaoLocal:
    def __init__(self, rest_key: str, timeout: float = 5.0):
        self._key, self._timeout = rest_key, timeout

    def search(self, query: str, x: float | None, y: float | None, radius: int | None) -> list[Place]:
        params: dict = {"query": query, "size": 15}
        if x is not None and y is not None:
            params.update(x=f"{x:.6f}", y=f"{y:.6f}", sort="distance")
            if radius:
                params["radius"] = radius
        try:
            r = httpx.get(KAKAO_URL, params=params, headers={"Authorization": f"KakaoAK {self._key}"}, timeout=self._timeout)
            r.raise_for_status()
            docs = r.json().get("documents", [])
        except Exception as e:  # no query text in errors/logs
            raise ApiError(503, "clinic_search_unavailable") from e
        out = []
        for d in docs:
            if "동물병원" not in (d.get("category_name") or ""):  # drop pet shops, pharmacies, ...
                continue
            dist = d.get("distance")
            out.append(Place(str(d.get("id", "")), d.get("place_name", ""), d.get("road_address_name", ""),
                             d.get("address_name", ""), d.get("phone", ""), int(dist) if dist else None,
                             d.get("place_url", "")))
        return out


def search(api: PlaceSearch | None, q: str | None, x: float | None, y: float | None, emergency: bool) -> list[dict]:
    if api is None:
        raise ApiError(503, "clinic_search_unavailable")
    q = (q or "").strip()
    if emergency:
        q, radius = "24시 동물병원", 10000
    else:
        if not q and (x is None or y is None):
            raise ApiError(400, "empty_query")
        if q and "동물병원" not in q and "병원" not in q:
            q = q + " 동물병원"  # a place name or a neighbourhood alone also returns non-hospitals
        q, radius = q or "동물병원", 3000 if x is not None else None
    return [p.view() for p in api.search(q[:60], x, y, radius)]


# ------------------------------------------------------------------ matching to our public-data master
def clinic_view(c: Clinic | None) -> dict | None:
    if c is None:
        return None
    return {"id": str(c.id), "source": c.source, "name": c.name, "road_address": c.road_address,
            "public_phone": c.public_phone, "business_status": c.business_status,
            "kakao_place_url": c.kakao_place_url, "is_partner": c.is_partner}


def match(db: DB, name: str, road_address: str | None, address: str | None) -> Clinic | None:
    """Find the public-data clinic for a Kakao result (used once as a lookup key, not stored).
    Same road address (to the building number) and a compatible name; ambiguous -> no match."""
    nk = name_key(name)
    rk = road_key(road_address) or None
    if rk:
        rows = db.execute(select(Clinic).where(Clinic.source == "public", Clinic.road_key == rk)).scalars().all()
        open_first = sorted(rows, key=lambda c: c.business_status != "open")
        same = [c for c in open_first if c.name_key == nk]
        if same:
            return same[0]
        close = [c for c in open_first if nk and (nk in c.name_key or c.name_key in nk)]
        if len(close) == 1:
            return close[0]
        live = [c for c in rows if c.business_status in ("open", "paused")]
        if len(live) == 1 and len(rows) <= 2:
            return live[0]
    if nk:  # moved building or a lot-number-only Kakao record: exact name in the same district
        rows = db.execute(select(Clinic).where(Clinic.source == "public", Clinic.name_key == nk,
                                               Clinic.business_status.in_(("open", "paused")))).scalars().all()
        parts = (road_address or address or "").split()
        prefix = (SIDO.get(parts[0], parts[0]) + parts[1]) if len(parts) >= 2 else ""
        cands = [c for c in rows if prefix and (c.road_key or "").startswith(prefix)]
        if len(cands) == 1:
            return cands[0]
    return None


def resolve_choice(db: DB, choice) -> Clinic | None:
    """ClinicChoice -> Clinic row (public record, or a manual one with the guardian-typed name)."""
    if choice is None:
        return None
    place_id = choice.kakao_place_id
    place_url = choice.kakao_place_url if choice.kakao_place_url and PLACE_URL.match(choice.kakao_place_url) else None
    if choice.clinic_id:
        c = db.get(Clinic, choice.clinic_id)
        if c is None:
            raise ApiError(422, "invalid_clinic", extra={"fields": ["clinic.clinic_id"]})
        if place_id and not c.kakao_place_id:  # id + url only (allowed by Kakao policy)
            c.kakao_place_id, c.kakao_place_url = place_id, place_url
        return c
    if place_id:
        existing = db.execute(select(Clinic).where(Clinic.source == "manual", Clinic.kakao_place_id == place_id,
                                                   Clinic.name_key == name_key(choice.manual_name))).scalars().first()
        if existing:
            return existing
    c = Clinic(id=uuid.uuid4(), source="manual", name=choice.manual_name, name_key=name_key(choice.manual_name),
               kakao_place_id=place_id, kakao_place_url=place_url if place_id else None)
    db.add(c)
    db.flush()
    return c
