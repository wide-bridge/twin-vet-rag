"""Dog profile input (per consultation) and life-stage derivation. See docs/03 §6."""
from __future__ import annotations

from enum import Enum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints


class LifeStage(str, Enum):
    puppy = "puppy"
    adult = "adult"
    senior = "senior"


class Neutered(str, Enum):
    yes = "yes"
    no = "no"
    unknown = "unknown"


class DogProfileIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    breed: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=40)]
    age_months: int = Field(ge=0, le=360)
    weight_kg: float | None = Field(default=None, ge=0.1, le=120.0)
    neutered: Neutered | None = None
    health_notes: Annotated[str, StringConstraints(strip_whitespace=True, max_length=300)] | None = None


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
