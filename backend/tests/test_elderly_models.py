from datetime import date

import pytest
from pydantic import ValidationError

from app.models.elderly import ElderlyProfileCreate, ElderlyProfileUpdate


def test_create_profile_strips_surrounding_whitespace() -> None:
    profile = ElderlyProfileCreate(
        elderly_id=" E001 ",
        full_name=" Margaret Lee ",
        date_of_birth=date(1948, 4, 12),
    )

    assert profile.elderly_id == "E001"
    assert profile.full_name == "Margaret Lee"


def test_create_profile_rejects_future_date_of_birth() -> None:
    with pytest.raises(ValidationError):
        ElderlyProfileCreate(
            elderly_id="E001",
            full_name="Margaret Lee",
            date_of_birth=date(2999, 1, 1),
        )


def test_update_profile_requires_at_least_one_field() -> None:
    with pytest.raises(ValidationError):
        ElderlyProfileUpdate()


@pytest.mark.parametrize("field", ["full_name", "date_of_birth"])
def test_update_profile_rejects_null_for_required_profile_fields(field: str) -> None:
    with pytest.raises(ValidationError):
        ElderlyProfileUpdate(**{field: None})
