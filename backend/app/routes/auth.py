from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, status

from app.config import Settings, get_settings
from app.dependencies import get_auth_service
from app.models.account import (
    AccountCreate,
    AccountRecord,
    BootstrapAdminCreate,
    LoginRequest,
    SessionResponse,
)
from app.models.common import SuccessResponse
from app.models.elderly import ElderlyId
from app.services.auth import (
    AuthConflict,
    AuthService,
    BootstrapUnavailable,
    InvalidCredentials,
    Principal,
    get_current_principal,
    require_admin_access,
    require_relationship_permission,
)


router = APIRouter(prefix="/api/auth", tags=["auth"])
AuthDependency = Annotated[AuthService, Depends(get_auth_service)]
PrincipalDependency = Annotated[Principal, Depends(get_current_principal)]
SettingsDependency = Annotated[Settings, Depends(get_settings)]


@router.post("/bootstrap", response_model=SuccessResponse[AccountRecord], status_code=201)
async def bootstrap_admin(
    request: BootstrapAdminCreate,
    service: AuthDependency,
    settings: SettingsDependency,
    bootstrap_secret: Annotated[str | None, Header(alias="X-Bootstrap-Secret")] = None,
) -> SuccessResponse[AccountRecord]:
    if not settings.auth_bootstrap_secret or not bootstrap_secret or bootstrap_secret != settings.auth_bootstrap_secret:
        raise HTTPException(status_code=404, detail="Not found")
    try:
        account = await service.bootstrap_admin(request)
    except BootstrapUnavailable as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return SuccessResponse(message="Administrator account created", data=account)


@router.post("/login", response_model=SuccessResponse[SessionResponse])
async def login(
    request: LoginRequest,
    service: AuthDependency,
) -> SuccessResponse[SessionResponse]:
    try:
        token, expires_at, account = await service.login(request)
    except InvalidCredentials as error:
        raise HTTPException(status_code=401, detail=str(error)) from error
    return SuccessResponse(
        message="Login successful",
        data=SessionResponse(access_token=token, expires_at=expires_at, account=account),
    )


@router.post("/logout", response_model=SuccessResponse[dict[str, bool]])
async def logout(
    service: AuthDependency,
    principal: PrincipalDependency,
) -> SuccessResponse[dict[str, bool]]:
    await service.revoke(principal)
    return SuccessResponse(message="Logged out", data={"logged_out": True})


@router.get("/me", response_model=SuccessResponse[AccountRecord])
async def me(
    service: AuthDependency,
    principal: PrincipalDependency,
) -> SuccessResponse[AccountRecord]:
    document = await service.accounts.find_one({"account_id": principal.account_id})
    if document is None:
        raise HTTPException(status_code=401, detail="Authentication is required")
    from app.services.auth import _account_record

    return SuccessResponse(message="Account retrieved", data=_account_record(document))


@router.post("/accounts", response_model=SuccessResponse[AccountRecord], status_code=201)
async def create_account(
    request: AccountCreate,
    service: AuthDependency,
    _: Annotated[Principal, Depends(require_admin_access)],
) -> SuccessResponse[AccountRecord]:
    try:
        account = await service.create_account(request, created_by=_.account_id)
    except AuthConflict as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return SuccessResponse(message="Account created", data=account)


@router.delete("/accounts/{account_id}", response_model=SuccessResponse[AccountRecord])
async def remove_family_account(
    account_id: str,
    service: AuthDependency,
    principal: Annotated[Principal, Depends(require_admin_access)],
) -> SuccessResponse[AccountRecord]:
    account = await service.remove_family_account(account_id, removed_by=principal.account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="Active family account was not found")
    return SuccessResponse(message="Family account removed", data=account)


@router.post("/websocket-ticket/{elderly_id}", response_model=SuccessResponse[dict[str, str]])
async def websocket_ticket(
    elderly_id: ElderlyId,
    service: AuthDependency,
    principal: Annotated[Principal, require_relationship_permission("read_dashboard")],
) -> SuccessResponse[dict[str, str]]:
    token = await service.create_websocket_ticket(principal, elderly_id)
    return SuccessResponse(message="WebSocket ticket created", data={"ticket": token})
