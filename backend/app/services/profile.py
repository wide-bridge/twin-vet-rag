"""Settings (P08): the dog profile and 주치동물병원, kept apart from consultations (docs/03 §1, §3).

Found by an anonymous profile key in its own HttpOnly cookie (only the hash is stored). Kept for
`profile_ttl_days` after the last use; every use moves expires_at forward. Consultations copy the dog
fields at creation, so editing the profile never changes past consultations.
"""
from __future__ import annotations

import secrets
from datetime import timedelta

from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session as DB

from ..api.errors import ApiError
from ..config import Settings
from ..db.models import Clinic, DogProfile
from ..domain.dog import MAX_AGE_MONTHS, DogProfileIn, age_from_birth
from . import clinics
from .consult import KST, now, sha256, today_kst


def resolve(db: DB, token: str | None) -> DogProfile | None:
    if not token:
        return None
    row = db.execute(select(DogProfile).where(DogProfile.token_hash == sha256(token))).scalar_one_or_none()
    if row is None or (row.expires_at is not None and row.expires_at <= now()):
        return None
    return row


def touch(db: DB, settings: Settings, row: DogProfile) -> None:
    t = now()
    row.last_used_at = t
    row.expires_at = t + timedelta(days=settings.profile_ttl_days)


def save(db: DB, settings: Settings, row: DogProfile | None, dog: DogProfileIn, *, set_clinic: bool, clinic_choice,
         clinic_phone: str | None, set_phone: bool) -> tuple[str | None, DogProfile]:
    """Create or update the profile. Returns (new cookie token or None, row)."""
    try:
        age = dog.age_on(today_kst())
    except ValueError:
        raise ApiError(422, "invalid_birth_date", extra={"fields": ["dog.birth_year"]})
    if set_phone and clinic_phone and not clinics.phone_ok(clinic_phone):
        raise ApiError(422, "invalid_phone", extra={"fields": ["clinic_phone"]})
    token = None
    if row is None:
        token = secrets.token_urlsafe(32)
        row = DogProfile(token_hash=sha256(token))
        db.add(row)
    row.name, row.breed, row.age_months = dog.name or None, dog.breed, age
    row.birth_date, row.age_basis = dog.birth_date(), dog.age_basis.value
    row.weight_kg = dog.weight_kg
    row.neutered = dog.neutered.value if dog.neutered else None
    row.health_notes = dog.health_notes or None
    row.updated_at = now()
    if set_clinic:
        c = clinics.resolve_choice(db, clinic_choice)
        row.clinic_id = c.id if c else None
        if c is None:
            row.clinic_phone = None
    if set_phone:
        row.clinic_phone = clinic_phone or None
    touch(db, settings, row)
    db.commit()
    return token, row


def current_age(row: DogProfile) -> int:
    """Age today: from the birth date, or the saved estimate grown by the months since it was saved."""
    today = today_kst()
    b = row.birth_date
    if b is not None:
        month = b.month if row.age_basis in ("day", "month") else None
        day = b.day if row.age_basis == "day" else None
        return min(MAX_AGE_MONTHS, age_from_birth(b.year, month, day, today))
    saved = row.updated_at.astimezone(KST).date()
    grown = (today.year - saved.year) * 12 + (today.month - saved.month) - (1 if today.day < saved.day else 0)
    return min(MAX_AGE_MONTHS, row.age_months + max(grown, 0))


def view(db: DB, row: DogProfile) -> dict:
    b, basis = row.birth_date, row.age_basis
    dog = {"name": row.name, "breed": row.breed, "age_months": current_age(row), "age_basis": basis,
           "birth_year": b.year if b else None,
           "birth_month": b.month if b and basis in ("day", "month") else None,
           "birth_day": b.day if b and basis == "day" else None,
           "weight_kg": float(row.weight_kg) if row.weight_kg is not None else None,
           "neutered": row.neutered, "health_notes": row.health_notes}
    clinic = db.get(Clinic, row.clinic_id) if row.clinic_id else None
    return {"dog": dog, "clinic": clinics.clinic_view(clinic), "clinic_phone": row.clinic_phone,
            "expires_at": row.expires_at.isoformat() if row.expires_at else None}


def remove(db: DB, row: DogProfile) -> None:
    from . import m03_hitl  # M03: HITL copies + derived knowledge are handled before the cascade deletes them
    m03_hitl.release_for_profiles(db, [row.id], "profile_deleted")
    db.execute(delete(DogProfile).where(DogProfile.id == row.id))  # consultations keep their snapshot (SET NULL)
    db.commit()


def purge_expired_profiles(db: DB) -> int:
    from . import m03_hitl
    expired = list(db.execute(text("SELECT id FROM dog_profile WHERE expires_at IS NOT NULL AND expires_at < now() FOR UPDATE")).scalars())
    m03_hitl.release_for_profiles(db, expired, "profile_expired")
    n = db.execute(text("DELETE FROM dog_profile WHERE id = ANY(:ids)"), {"ids": expired}).rowcount if expired else 0
    db.commit()
    return n
