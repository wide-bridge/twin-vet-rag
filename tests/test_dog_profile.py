import pytest
from pydantic import ValidationError

from backend.app.domain.dog import DogProfileIn, LifeStage, derive_life_stage


@pytest.mark.parametrize(
    ("months", "expected"),
    [(0, LifeStage.puppy), (11, LifeStage.puppy), (12, LifeStage.adult), (83, LifeStage.adult), (84, LifeStage.senior), (371, LifeStage.senior)],
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
        {"breed": "푸들", "age_months": 372},
        {"breed": "푸들", "age_months": 10, "weight_kg": 0},
        {"breed": "푸들", "age_months": 10, "health_notes": "가" * 301},
        {"breed": "푸들", "age_months": 10, "species": "cat"},  # no species field allowed
        {"breed": "푸들", "age_months": 10, "life_stage": "senior"},  # life stage is server-derived
    ],
)
def test_profile_rejects_invalid(payload):
    with pytest.raises(ValidationError):
        DogProfileIn(**payload)


# ------------------------------------------------------------------ birth date (as far as known) or estimate
from datetime import date  # noqa: E402

from backend.app.domain.dog import AgeBasis, age_from_birth  # noqa: E402

TODAY = date(2026, 11, 11)


@pytest.mark.parametrize("payload, basis, months", [
    ({"birth_year": 2021, "birth_month": 3, "birth_day": 15}, AgeBasis.day, 67),     # 5y 7m
    ({"birth_year": 2021, "birth_month": 11, "birth_day": 12}, AgeBasis.day, 59),    # birthday tomorrow: not yet 5y
    ({"birth_year": 2021, "birth_month": 11, "birth_day": 11}, AgeBasis.day, 60),    # birthday today: 5y
    ({"birth_year": 2021, "birth_month": 3}, AgeBasis.month, 68),
    ({"birth_year": 2021}, AgeBasis.year, 64),                                       # assumes July 1
    ({"birth_year": 2026}, AgeBasis.year, 4),
    ({"birth_year": 2026, "birth_month": 11}, AgeBasis.month, 0),
    ({"age_months": 54}, AgeBasis.estimate, 54),
])
def test_age_from_birth_or_estimate(payload, basis, months):
    p = DogProfileIn(breed="푸들", **payload)
    assert p.age_basis is basis and p.age_on(TODAY) == months
    assert (p.birth_date() is None) == (basis is AgeBasis.estimate)


@pytest.mark.parametrize("payload", [
    {},                                                         # neither birth date nor estimate
    {"birth_year": 2021, "age_months": 10},                     # both
    {"birth_month": 3, "age_months": 10},                       # month without year
    {"birth_year": 2021, "birth_day": 3},                       # day without month
    {"birth_year": 2023, "birth_month": 2, "birth_day": 29},    # no such date
    {"birth_year": 2021, "birth_month": 13},
])
def test_birth_input_rejected(payload):
    with pytest.raises(ValidationError):
        DogProfileIn(breed="푸들", **payload)


@pytest.mark.parametrize("payload", [
    {"birth_year": 2027}, {"birth_year": 2026, "birth_month": 12}, {"birth_year": 2026, "birth_month": 11, "birth_day": 12},
    {"birth_year": 1995, "birth_month": 1},                     # over 30y 11m
])
def test_future_or_too_old_birth_rejected(payload):
    with pytest.raises(ValueError):
        DogProfileIn(breed="푸들", **payload).age_on(TODAY)


def test_age_from_birth_never_negative():
    assert age_from_birth(2026, None, None, date(2026, 2, 1)) == 0


# ------------------------------------------------------------------ name (optional, only used to address the dog)
from backend.app.rag.prompts import DogInfo  # noqa: E402


def test_name_optional_trimmed_and_limited():
    assert DogProfileIn(breed="푸들", age_months=30).name is None
    assert DogProfileIn(breed="푸들", age_months=30, name="  두부 ").name == "두부"
    assert DogProfileIn(breed="푸들", age_months=30, name="Max 2").name == "Max 2"
    for bad in ["", "   ", "가" * 21, "<시스템>", "두\n부", "{x}", "a\b"]:
        with pytest.raises(ValidationError):
            DogProfileIn(breed="푸들", age_months=30, name=bad)


def test_prompt_block_carries_name_as_data():
    assert DogInfo("푸들", 137, name="두부").block().splitlines()[0] == "- 이름: 두부"
    assert "이름" not in DogInfo("푸들", 137).block()
