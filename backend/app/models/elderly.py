from datetime import date, datetime
from typing import Annotated, Self

from pydantic import BaseModel, Field, StringConstraints, field_validator, model_validator


ElderlyId = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=50,
        pattern=r"^[A-Za-z0-9_-]+$",
    ),
]
RequiredText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=120),
]


class ElderlyProfileCreate(BaseModel):
    elderly_id: ElderlyId
    full_name: RequiredText
    date_of_birth: date
    phone_number: str | None = Field(default=None, max_length=30)
    address: str | None = Field(default=None, max_length=300)
    emergency_contact_name: str | None = Field(default=None, max_length=120)
    emergency_contact_phone: str | None = Field(default=None, max_length=30)
    medical_notes: str | None = Field(default=None, max_length=1000)

    @field_validator("date_of_birth")
    @classmethod
    def date_of_birth_cannot_be_in_the_future(cls, value: date) -> date:
        if value > date.today():
            raise ValueError("date_of_birth cannot be in the future")
        return value


class ElderlyProfileUpdate(BaseModel):
    full_name: RequiredText | None = None
    date_of_birth: date | None = None
    phone_number: str | None = Field(default=None, max_length=30)
    address: str | None = Field(default=None, max_length=300)
    emergency_contact_name: str | None = Field(default=None, max_length=120)
    emergency_contact_phone: str | None = Field(default=None, max_length=30)
    medical_notes: str | None = Field(default=None, max_length=1000)

    @field_validator("full_name", "date_of_birth")
    @classmethod
    def required_profile_fields_cannot_be_cleared(cls, value: object) -> object:
        if value is None:
            raise ValueError("Required profile fields cannot be null")
        return value

    @field_validator("date_of_birth")
    @classmethod
    def date_of_birth_cannot_be_in_the_future(cls, value: date | None) -> date | None:
        if value is not None and value > date.today():
            raise ValueError("date_of_birth cannot be in the future")
        return value

    @model_validator(mode="after")
    def request_must_contain_an_update(self) -> Self:
        if not self.model_fields_set:
            raise ValueError("At least one field must be supplied")
        return self


class ElderlyProfile(ElderlyProfileCreate):
    active: bool
    created_at: datetime
    updated_at: datetime
