import pytest
from pydantic import ValidationError

from backend.app.domain.dog import DogProfileIn, LifeStage, derive_life_stage


@pytest.mark.parametrize(
    ("months", "expected"),
    [(0, LifeStage.puppy), (11, LifeStage.puppy), (12, LifeStage.adult), (83, LifeStage.adult), (84, LifeStage.senior), (360, LifeStage.senior)],
)
def test_life_stage_boundaries(months, expected):
    assert derive_life_stage(months) is expected


def test_profile_required_and_trimmed():
    p = DogProfileIn(breed="  말티즈 ", age_months=123)
    assert p.breed == "말티즈" and p.weight_kg is None and p.neutered is None


@pytest.mark.parametrize(
    "payload",
    [
        {"age_months": 10},  # breed missing
        {"breed": "   ", "age_months": 10},  # blank breed
        {"breed": "푸들", "age_months": -1},
        {"breed": "푸들", "age_months": 361},
        {"breed": "푸들", "age_months": 10, "weight_kg": 0},
        {"breed": "푸들", "age_months": 10, "health_notes": "가" * 301},
        {"breed": "푸들", "age_months": 10, "species": "cat"},  # no species field allowed
        {"breed": "푸들", "age_months": 10, "life_stage": "senior"},  # life stage is server-derived
    ],
)
def test_profile_rejects_invalid(payload):
    with pytest.raises(ValidationError):
        DogProfileIn(**payload)
