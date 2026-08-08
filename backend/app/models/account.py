from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, StringConstraints
from typing_extensions import Annotated


AccountRole = Literal["admin", "staff", "elderly", "family"]
AccountStatus = Literal["active", "disabled"]
LoginName = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=3, max_length=80),
]
Password = Annotated[str, StringConstraints(min_length=12, max_length=200)]


class AccountCreate(BaseModel):
    login_name: LoginName
    display_name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
    password: Password
    role: AccountRole


class BootstrapAdminCreate(BaseModel):
    login_name: LoginName
    display_name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
    password: Password


class LoginRequest(BaseModel):
    login_name: LoginName
    password: Password


class AccountRecord(BaseModel):
    account_id: str
    login_name: str
    display_name: str
    role: AccountRole
    status: AccountStatus
    created_at: datetime
    updated_at: datetime


class SessionResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_at: datetime
    account: AccountRecord
