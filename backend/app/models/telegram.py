from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.models.elderly import ElderlyId


class TelegramLinkRequest(BaseModel):
    expires_in_seconds: int = Field(default=600, ge=60, le=3600)


class TelegramLinkResponse(BaseModel):
    code: str
    expires_at: datetime


class TelegramBindingRecord(BaseModel):
    account_id: str
    telegram_user_id: str
    chat_type: Literal["private"]
    linked_at: datetime


class TelegramBindRequest(BaseModel):
    code: str = Field(min_length=16, max_length=200)
    telegram_user_id: str = Field(min_length=1, max_length=80)
    chat_id: str = Field(min_length=1, max_length=100)
    chat_type: Literal["private"]


class TelegramStatusRequest(BaseModel):
    telegram_user_id: str = Field(min_length=1, max_length=80)
    elderly_id: ElderlyId


class TelegramAccessRequest(BaseModel):
    telegram_user_id: str = Field(min_length=1, max_length=80)
    elderly_id: ElderlyId


class TelegramStatusResponse(BaseModel):
    elderly_id: str
    current_risk: Literal["normal", "warning", "emergency"]
    device_status: str
    active_alert_count: int
    latest_reading_at: datetime | None = None


class AdminTelegramBindingRecord(BaseModel):
    account_id: str
    account_login_name: str
    account_display_name: str
    telegram_user_id: str
    chat_type: Literal["private"]
    linked_at: datetime
    receive_telegram_alerts: bool
