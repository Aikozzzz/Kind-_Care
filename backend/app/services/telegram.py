import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

from pymongo.asynchronous.database import AsyncDatabase

from app.models.telegram import TelegramBindRequest, TelegramLinkResponse, TelegramStatusResponse
from app.services.auth import Principal, RelationshipDenied, authorize_relationship


class TelegramConflict(Exception):
    pass


class TelegramDenied(Exception):
    pass


class TelegramService:
    def __init__(self, database: AsyncDatabase) -> None:
        self.database = database

    async def create_link(self, principal: Principal, expires_in_seconds: int) -> TelegramLinkResponse:
        return await self.create_link_for_account(
            principal.account_id, expires_in_seconds, family_only=False
        )

    async def create_link_for_account(
        self, account_id: str, expires_in_seconds: int, *, family_only: bool = True
    ) -> TelegramLinkResponse:
        account = await self.database.accounts.find_one(
            {"account_id": account_id, "status": "active"},
            {"account_id": 1, "role": 1},
        )
        if account is None or (family_only and account.get("role") != "family"):
            raise TelegramDenied("An active family account is required")
        now = datetime.now(UTC)
        code = secrets.token_urlsafe(24)
        expires_at = now + timedelta(seconds=expires_in_seconds)
        await self.database.telegram_link_tokens.insert_one(
            {
                "token_hash": hashlib.sha256(code.encode()).hexdigest(),
                "account_id": account_id,
                "created_at": now,
                "expires_at": expires_at,
                "consumed_at": None,
            }
        )
        return TelegramLinkResponse(code=code, expires_at=expires_at)

    async def bind(self, request: TelegramBindRequest) -> None:
        now = datetime.now(UTC)
        token_hash = hashlib.sha256(request.code.encode()).hexdigest()
        token = await self.database.telegram_link_tokens.find_one_and_update(
            {
                "token_hash": token_hash,
                "consumed_at": None,
                "expires_at": {"$gt": now},
            },
            {"$set": {"consumed_at": now}},
        )
        if token is None:
            raise TelegramDenied("Link code is invalid or expired")
        await self.database.telegram_bindings.update_one(
            {"telegram_user_id": request.telegram_user_id},
            {
                "$set": {
                    "account_id": token["account_id"],
                    "telegram_user_id": request.telegram_user_id,
                    "chat_id": request.chat_id,
                    "chat_type": request.chat_type,
                    "linked_at": now,
                    "revoked_at": None,
                }
            },
            upsert=True,
        )

    async def revoke(self, principal: Principal) -> None:
        await self.database.telegram_bindings.update_many(
            {"account_id": principal.account_id, "revoked_at": None},
            {"$set": {"revoked_at": datetime.now(UTC)}},
        )

    async def revoke_binding(self, telegram_user_id: str) -> bool:
        result = await self.database.telegram_bindings.update_one(
            {"telegram_user_id": telegram_user_id, "revoked_at": None},
            {"$set": {"revoked_at": datetime.now(UTC)}},
        )
        return result.modified_count == 1

    async def status_for_telegram(
        self, telegram_user_id: str, elderly_id: str
    ) -> TelegramStatusResponse:
        binding = await self.database.telegram_bindings.find_one(
            {"telegram_user_id": telegram_user_id, "revoked_at": None}
        )
        if binding is None:
            raise TelegramDenied("Status is unavailable")
        account = await self.database.accounts.find_one(
            {"account_id": binding["account_id"], "status": "active"}
        )
        if account is None:
            raise TelegramDenied("Status is unavailable")
        principal = Principal(
            account_id=str(account["account_id"]),
            login_name=str(account["login_name"]),
            display_name=str(account["display_name"]),
            role=str(account["role"]),
        )
        try:
            await authorize_relationship(
                self.database, principal, elderly_id, "query_telegram_status"
            )
        except RelationshipDenied as error:
            raise TelegramDenied("Status is unavailable") from error
        profile = await self.database.elderly_profiles.find_one(
            {"elderly_id": elderly_id, "active": True}, {"elderly_id": 1}
        )
        if profile is None:
            raise TelegramDenied("Status is unavailable")
        latest_health = await self.database.health_logs.find_one(
            {"elderly_id": elderly_id}, sort=[("recorded_at", -1), ("event_id", -1)]
        )
        latest_device = await self.database.device_status.find_one({"elderly_id": elderly_id})
        active_alerts = await self.database.alerts.count_documents(
            {"elderly_id": elderly_id, "status": {"$in": ["unresolved", "acknowledged"]}}
        )
        risk = "normal"
        if active_alerts:
            emergency = await self.database.alerts.find_one(
                {"elderly_id": elderly_id, "status": {"$in": ["unresolved", "acknowledged"]}, "severity": "emergency"}
            )
            risk = "emergency" if emergency else "warning"
        elif latest_health and latest_health.get("risk_level") in {"warning", "emergency"}:
            risk = latest_health["risk_level"]
        return TelegramStatusResponse(
            elderly_id=elderly_id,
            current_risk=risk,
            device_status=str((latest_device or {}).get("status", "unavailable")),
            active_alert_count=active_alerts,
            latest_reading_at=(latest_health or {}).get("recorded_at"),
        )
