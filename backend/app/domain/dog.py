"""Dog profile input (per consultation) and life-stage derivation. See docs/03 §6."""
from __future__ import annotations

import calendar
from datetime import date
from enum import Enum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

MAX_AGE_MONTHS = 371  # 30y 11m (docs/03 §6)


class LifeStage(str, Enum):
    puppy = "puppy"
    adult = "adult"
    senior = "senior"


class Neutered(str, Enum):
    yes = "yes"
    no = "no"
    unknown = "unknown"


class AgeBasis(str, Enum):
    """How the age is known: birth date to the day / to the month / year only, or the guardian's estimate."""
    day = "day"
    month = "month"
    year = "year"
    estimate = "estimate"


RELIABLE_AGE = (AgeBasis.day, AgeBasis.month)


def age_from_birth(year: int, month: int | None, day: int | None, today: date) -> int:
    """Whole months from birth to today. Year only assumes a mid-year (July 1) birth."""
    m = month or 7
    months = (today.year - year) * 12 + (today.month - m)
    if month and day and today.day < day:
        months -= 1
    return max(months, 0)


class DogProfileIn(BaseModel):
    """Age is given either as a birth date known as far as the guardian knows it (year -> month -> day),
    or, when unknown, as an estimated age in months. Exactly one of the two."""
    model_config = ConfigDict(extra="forbid")

    # optional; only used to address the dog. No control characters or markup-like brackets (it goes into prompts as data)
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=20,
                                           pattern=r"^[^\x00-\x1f\x7f<>\[\]{}`\x5c]+$")] | None = None
    breed: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=40)]
    birth_year: int | None = Field(default=None, ge=1990, le=2100)
    birth_month: int | None = Field(default=None, ge=1, le=12)
    birth_day: int | None = Field(default=None, ge=1, le=31)
    age_months: int | None = Field(default=None, ge=0, le=MAX_AGE_MONTHS)  # estimate, only without a birth year
    weight_kg: float | None = Field(default=None, ge=0.1, le=120.0)
    neutered: Neutered | None = None
    health_notes: Annotated[str, StringConstraints(strip_whitespace=True, max_length=300)] | None = None

    @model_validator(mode="after")
    def _age(self) -> "DogProfileIn":
        if self.birth_year is None:
            if self.birth_month is not None or self.birth_day is not None:
                raise ValueError("birth_month/birth_day need birth_year")
            if self.age_months is None:
                raise ValueError("birth date or estimated age_months is required")
            return self
        if self.age_months is not None:
            raise ValueError("give either a birth date or an estimated age, not both")
        if self.birth_day is not None and self.birth_month is None:
            raise ValueError("birth_day needs birth_month")
        if self.birth_day is not None and self.birth_day > calendar.monthrange(self.birth_year, self.birth_month)[1]:
            raise ValueError("birth date does not exist")
        return self

    @property
    def age_basis(self) -> AgeBasis:
        if self.birth_year is None:
            return AgeBasis.estimate
        return AgeBasis.day if self.birth_day else AgeBasis.month if self.birth_month else AgeBasis.year

    def birth_date(self) -> date | None:
        """Stored as a DATE; unknown parts are 1 and age_basis says which parts are real."""
        if self.birth_year is None:
            return None
        return date(self.birth_year, self.birth_month or 1, self.birth_day or 1)

    def age_on(self, today: date) -> int:
        """Age in months; raises ValueError for a future birth date or an age over the limit."""
        if self.birth_year is None:
            return self.age_months  # type: ignore[return-value]
        y, m, d = self.birth_year, self.birth_month, self.birth_day
        future = (y > today.year
                  or (m is not None and (y, m) > (today.year, today.month))
                  or (d is not None and date(y, m, d) > today))  # type: ignore[arg-type]
        if future:
            raise ValueError("birth date is in the future")
        months = age_from_birth(y, m, d, today)
        if months > MAX_AGE_MONTHS:
            raise ValueError("age over 30 years 11 months")
        return months


def derive_life_stage(age_months: int, puppy_max_months: int = 12, senior_min_months: int = 84) -> LifeStage:
    """Provisional thresholds for the *search filter only* (docs/03 §6) — not a veterinary
    life-stage judgement, and breed/size differences are not reflected."""
    if age_months < 0:
        raise ValueError("age_months must be >= 0")
    if puppy_max_months >= senior_min_months:
        raise ValueError("puppy threshold must be below senior threshold")
    if age_months < puppy_max_months:
        return LifeStage.puppy
    if age_months >= senior_min_months:
        return LifeStage.senior
    return LifeStage.adult


# Source meta.lifeCycle values in AI Hub dataset 59 (verified over all 21,606 QA files).
SOURCE_LIFECYCLE_MAP: dict[str, LifeStage] = {
    "자견": LifeStage.puppy,
    "성견": LifeStage.adult,
    "노령견": LifeStage.senior,
}
