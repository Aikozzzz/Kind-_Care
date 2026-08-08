from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query

from app.config import Settings, get_settings
from app.dependencies import get_database
from app.models.common import SuccessResponse
from app.models.telegram import (
    TelegramAccessRequest,
    TelegramBindRequest,
    TelegramLinkRequest,
    TelegramLinkResponse,
    TelegramStatusRequest,
    TelegramStatusResponse,
    AdminTelegramBindingRecord,
)
from app.services.auth import Principal, get_current_principal, require_admin_access
from app.services.telegram import TelegramDenied, TelegramService
from pymongo.asynchronous.database import AsyncDatabase


router = APIRouter(prefix="/api/telegram", tags=["telegram"])
DatabaseDependency = Annotated[AsyncDatabase, Depends(get_database)]
PrincipalDependency = Annotated[Principal, Depends(get_current_principal)]
SettingsDependency = Annotated[Settings, Depends(get_settings)]
AdminDependency = Annotated[Principal, Depends(require_admin_access)]


def _service(database: AsyncDatabase) -> TelegramService:
    return TelegramService(database)


@router.post("/link", response_model=SuccessResponse[TelegramLinkResponse])
async def create_telegram_link(
    request: TelegramLinkRequest,
    database: DatabaseDependency,
    principal: PrincipalDependency,
) -> SuccessResponse[TelegramLinkResponse]:
    code = await _service(database).create_link(principal, request.expires_in_seconds)
    return SuccessResponse(message="Telegram link code created", data=code)


@router.post("/admin/link/{account_id}", response_model=SuccessResponse[TelegramLinkResponse])
async def create_family_telegram_link(
    account_id: str,
    request: TelegramLinkRequest,
    database: DatabaseDependency,
    _: AdminDependency,
) -> SuccessResponse[TelegramLinkResponse]:
    try:
        code = await _service(database).create_link_for_account(
            account_id, request.expires_in_seconds
        )
    except TelegramDenied as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return SuccessResponse(message="Family Telegram link code created", data=code)


@router.get("/admin/bindings", response_model=SuccessResponse[list[AdminTelegramBindingRecord]])
async def list_family_telegram_bindings(
    database: DatabaseDependency,
    _: AdminDependency,
    elderly_id: Annotated[str, Query(min_length=1, max_length=50)],
) -> SuccessResponse[list[AdminTelegramBindingRecord]]:
    profile = await database.elderly_profiles.find_one(
        {"elderly_id": elderly_id}, {"elderly_id": 1}
    )
    if profile is None:
        raise HTTPException(status_code=404, detail="Resident was not found")
    relationships = await database.account_elderly_relationships.find(
        {
            "elderly_id": elderly_id,
            "status": "active",
        },
        {"account_id": 1, "permissions": 1},
    ).to_list(length=100)
    records: list[AdminTelegramBindingRecord] = []
    for relationship in relationships:
        account = await database.accounts.find_one(
            {"account_id": relationship["account_id"], "status": "active"},
            {"account_id": 1, "login_name": 1, "display_name": 1},
        )
        if account is None:
            continue
        binding = await database.telegram_bindings.find_one(
            {"account_id": relationship["account_id"], "revoked_at": None},
            {"telegram_user_id": 1, "chat_type": 1, "linked_at": 1},
        )
        if binding is None:
            continue
        records.append(
            AdminTelegramBindingRecord(
                account_id=str(account["account_id"]),
                account_login_name=str(account["login_name"]),
                account_display_name=str(account["display_name"]),
                telegram_user_id=str(binding["telegram_user_id"]),
                chat_type=str(binding["chat_type"]),
                linked_at=binding["linked_at"],
                receive_telegram_alerts="receive_telegram_alerts"
                in relationship.get("permissions", []),
            )
        )
    return SuccessResponse(message="Telegram bindings retrieved", data=records)


@router.delete("/admin/bindings/{telegram_user_id}", response_model=SuccessResponse[dict[str, bool]])
async def revoke_family_telegram_binding(
    telegram_user_id: str,
    database: DatabaseDependency,
    _: AdminDependency,
) -> SuccessResponse[dict[str, bool]]:
    revoked = await _service(database).revoke_binding(telegram_user_id)
    if not revoked:
        raise HTTPException(status_code=404, detail="Telegram binding was not found")
    return SuccessResponse(
        message="Telegram binding revoked", data={"revoked": True}
    )


@router.post("/bind", response_model=SuccessResponse[dict[str, bool]])
async def bind_telegram(
    request: TelegramBindRequest,
    database: DatabaseDependency,
    settings: SettingsDependency,
    service_token: Annotated[str | None, Header(alias="X-Telegram-Service-Token")] = None,
) -> SuccessResponse[dict[str, bool]]:
    if not settings.telegram_service_token or service_token != settings.telegram_service_token:
        raise HTTPException(status_code=404, detail="Not found")
    try:
        await _service(database).bind(request)
    except TelegramDenied as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return SuccessResponse(message="Telegram account linked", data={"linked": True})


@router.post("/status", response_model=SuccessResponse[TelegramStatusResponse])
async def telegram_status(
    request: TelegramStatusRequest,
    database: DatabaseDependency,
    settings: SettingsDependency,
    service_token: Annotated[str | None, Header(alias="X-Telegram-Service-Token")] = None,
) -> SuccessResponse[TelegramStatusResponse]:
    if not settings.telegram_service_token or service_token != settings.telegram_service_token:
        raise HTTPException(status_code=404, detail="Not found")
    try:
        result = await _service(database).status_for_telegram(
            request.telegram_user_id, request.elderly_id
        )
    except TelegramDenied as error:
        raise HTTPException(status_code=404, detail="Status is unavailable") from error
    return SuccessResponse(message="Status retrieved", data=result)


@router.post("/request", response_model=SuccessResponse[dict[str, str]])
async def request_telegram_access(
    request: TelegramAccessRequest,
    database: DatabaseDependency,
    settings: SettingsDependency,
    service_token: Annotated[str | None, Header(alias="X-Telegram-Service-Token")] = None,
) -> SuccessResponse[dict[str, str]]:
    if not settings.telegram_service_token or service_token != settings.telegram_service_token:
        raise HTTPException(status_code=404, detail="Not found")
    binding = await database.telegram_bindings.find_one(
        {"telegram_user_id": request.telegram_user_id, "revoked_at": None},
        {"account_id": 1},
    )
    profile = await database.elderly_profiles.find_one(
        {"elderly_id": request.elderly_id, "active": True}, {"elderly_id": 1}
    )
    if binding is None or profile is None:
        raise HTTPException(status_code=404, detail="Access request could not be recorded")
    existing = await database.access_requests.find_one(
        {
            "account_id": binding["account_id"],
            "elderly_id": request.elderly_id,
            "status": "pending",
        }
    )
    if existing is None:
        from datetime import UTC, datetime
        from uuid import uuid4

        await database.access_requests.insert_one(
            {
                "request_id": str(uuid4()),
                "account_id": binding["account_id"],
                "elderly_id": request.elderly_id,
                "permissions": [
                    "read_dashboard",
                    "query_telegram_status",
                    "receive_telegram_alerts",
                ],
                "status": "pending",
                "created_at": datetime.now(UTC),
                "reviewed_at": None,
            }
        )
    return SuccessResponse(
        message="Access request submitted",
        data={"status": "pending"},
    )


@router.post("/unlink", response_model=SuccessResponse[dict[str, bool]])
async def unlink_telegram(
    database: DatabaseDependency,
    principal: PrincipalDependency,
) -> SuccessResponse[dict[str, bool]]:
    await _service(database).revoke(principal)
    return SuccessResponse(message="Telegram account unlinked", data={"unlinked": True})
