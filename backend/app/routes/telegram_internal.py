import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from pymongo import ReturnDocument
from pymongo.asynchronous.database import AsyncDatabase

from app.config import Settings, get_settings
from app.dependencies import get_database


router = APIRouter(prefix="/api/internal/telegram", tags=["telegram-internal"])
DatabaseDependency = Annotated[AsyncDatabase, Depends(get_database)]


class DeliveryComplete(BaseModel):
    event_id: str = Field(min_length=1, max_length=120)
    telegram_user_id: str = Field(min_length=1, max_length=80)
    success: bool
    error: str | None = Field(default=None, max_length=200)


async def require_telegram_service(
    settings: Annotated[Settings, Depends(get_settings)],
    token: Annotated[str | None, Header(alias="X-Telegram-Service-Token")] = None,
) -> None:
    if not settings.telegram_service_token or token is None or not secrets.compare_digest(
        token, settings.telegram_service_token
    ):
        raise HTTPException(status_code=404, detail="Not found")


ServiceDependency = Annotated[None, Depends(require_telegram_service)]


@router.post("/claim")
async def claim_notification(
    database: DatabaseDependency,
    _: ServiceDependency,
) -> dict[str, object] | None:
    now = datetime.now(UTC)
    lease_until = now + timedelta(seconds=60)
    owner = secrets.token_urlsafe(12)
    event = await database.alert_notification_events.find_one_and_update(
        {
            "$or": [
                {"status": "pending", "next_attempt_at": {"$lte": now}},
                {"status": "failed", "next_attempt_at": {"$lte": now}},
                {"status": "inflight", "lease_expires_at": {"$lte": now}},
            ]
        },
        {
            "$set": {
                "status": "inflight",
                "lease_owner": owner,
                "lease_expires_at": lease_until,
                "updated_at": now,
            },
            "$inc": {"attempt_count": 1},
        },
        sort=[("created_at", 1)],
        return_document=ReturnDocument.AFTER,
    )
    if event is None:
        return None
    alert = await database.alerts.find_one({"alert_id": event["alert_id"]})
    if alert is None:
        await database.alert_notification_events.update_one(
            {"notification_event_id": event["notification_event_id"]},
            {"$set": {"status": "sent", "updated_at": now}},
        )
        return None
    profile = await database.elderly_profiles.find_one(
        {"elderly_id": event["elderly_id"], "active": True}, {"elderly_id": 1}
    )
    if profile is None:
        await database.alert_notification_events.update_one(
            {"notification_event_id": event["notification_event_id"]},
            {"$set": {"status": "sent", "updated_at": now}},
        )
        return None
    relationships = await database.account_elderly_relationships.find(
        {
            "elderly_id": event["elderly_id"],
            "status": "active",
            "permissions": "receive_telegram_alerts",
        }
    ).to_list(length=100)
    deliveries: list[dict[str, str]] = []
    for relationship in relationships:
        binding = await database.telegram_bindings.find_one(
            {"account_id": relationship["account_id"], "revoked_at": None},
            {"telegram_user_id": 1, "chat_id": 1},
        )
        if binding is None:
            continue
        delivery_id = hashlib.sha256(
            f'{event["notification_event_id"]}:{binding["telegram_user_id"]}'.encode()
        ).hexdigest()
        await database.telegram_deliveries.update_one(
            {"notification_event_id": event["notification_event_id"], "telegram_user_id": binding["telegram_user_id"]},
            {
                "$setOnInsert": {
                    "delivery_id": delivery_id,
                    "notification_event_id": event["notification_event_id"],
                    "telegram_user_id": binding["telegram_user_id"],
                    "chat_id": binding.get("chat_id", binding["telegram_user_id"]),
                    "status": "pending",
                    "created_at": now,
                }
            },
            upsert=True,
        )
        existing = await database.telegram_deliveries.find_one(
            {"notification_event_id": event["notification_event_id"], "telegram_user_id": binding["telegram_user_id"]}
        )
        if existing and existing.get("status") != "sent":
            deliveries.append(
                {
                    "telegram_user_id": binding["telegram_user_id"],
                    "chat_id": str(existing.get("chat_id", binding["telegram_user_id"])),
                }
            )
    if not deliveries:
        await database.alert_notification_events.update_one(
            {"notification_event_id": event["notification_event_id"]},
            {"$set": {"status": "sent", "updated_at": now}},
        )
        return None
    return {
        "event_id": str(event["notification_event_id"]),
        "alert_id": str(alert["alert_id"]),
        "elderly_id": str(alert["elderly_id"]),
        "alert_type": str(alert.get("alert_type", "alert")),
        "severity": str(alert.get("severity", "warning")),
        "created_at": alert.get("created_at"),
        "deliveries": deliveries,
    }


@router.post("/complete")
async def complete_notification(
    request: DeliveryComplete,
    database: DatabaseDependency,
    _: ServiceDependency,
) -> dict[str, bool]:
    now = datetime.now(UTC)
    delivery_filter = {
        "notification_event_id": request.event_id,
        "telegram_user_id": request.telegram_user_id,
    }
    await database.telegram_deliveries.update_one(
        delivery_filter,
        {
            "$set": {
                "status": "sent" if request.success else "failed",
                "error": None if request.success else "delivery failed",
                "sent_at": now if request.success else None,
                "updated_at": now,
            }
        },
    )
    if request.success:
        pending = await database.telegram_deliveries.count_documents(
            {"notification_event_id": request.event_id, "status": {"$ne": "sent"}}
        )
        if pending == 0:
            await database.alert_notification_events.update_one(
                {"notification_event_id": request.event_id},
                {"$set": {"status": "sent", "updated_at": now}},
            )
    else:
        await database.alert_notification_events.update_one(
            {"notification_event_id": request.event_id},
            {
                "$set": {
                    "status": "failed",
                    "next_attempt_at": now + timedelta(seconds=30),
                    "updated_at": now,
                }
            },
        )
    return {"accepted": True}
