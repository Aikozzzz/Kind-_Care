import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pymongo.asynchronous.database import AsyncDatabase
from pymongo import ReturnDocument
from starlette.requests import HTTPConnection

from app.models.account import AccountCreate, AccountRecord, BootstrapAdminCreate, LoginRequest
from app.config import get_settings


PASSWORD_ITERATIONS = 600_000
BEARER = HTTPBearer(auto_error=False)


class AuthConflict(Exception):
    pass


class InvalidCredentials(Exception):
    pass


class BootstrapUnavailable(Exception):
    pass


class RelationshipDenied(Exception):
    pass


@dataclass(frozen=True)
class Principal:
    account_id: str
    login_name: str
    display_name: str
    role: str
    session_id: str | None = None

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"

    @property
    def is_service(self) -> bool:
        return self.role == "service"


def normalize_login_name(value: str) -> str:
    return " ".join(value.strip().casefold().split())


def hash_password(password: str, *, salt: bytes | None = None) -> str:
    actual_salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode(), actual_salt, PASSWORD_ITERATIONS
    )
    return f"pbkdf2_sha256${PASSWORD_ITERATIONS}${actual_salt.hex()}${digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, iterations, salt_hex, digest_hex = encoded.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        actual = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), bytes.fromhex(salt_hex), int(iterations)
        ).hex()
    except (TypeError, ValueError):
        return False
    return hmac.compare_digest(actual, digest_hex)


def _account_record(document: dict[str, object]) -> AccountRecord:
    return AccountRecord(
        account_id=str(document["account_id"]),
        login_name=str(document["login_name"]),
        display_name=str(document["display_name"]),
        role=str(document["role"]),
        status=str(document["status"]),
        created_at=document["created_at"],
        updated_at=document["updated_at"],
    )


class AuthService:
    def __init__(self, database: AsyncDatabase, session_seconds: int = 3600) -> None:
        self.database = database
        self.accounts = database.accounts
        self.sessions = database.auth_sessions
        self.session_seconds = session_seconds

    async def create_account(self, request: AccountCreate, *, created_by: str) -> AccountRecord:
        now = datetime.now(UTC)
        login_name = normalize_login_name(request.login_name)
        document = {
            "account_id": str(uuid4()),
            "login_name": login_name,
            "display_name": request.display_name.strip(),
            "role": request.role,
            "status": "active",
            "password_hash": hash_password(request.password),
            "auth_version": 1,
            "created_by_account_id": created_by,
            "created_at": now,
            "updated_at": now,
        }
        try:
            await self.accounts.insert_one(document)
        except Exception as error:
            if getattr(error, "code", None) == 11000:
                raise AuthConflict("Login name is already in use") from error
            raise
        return _account_record(document)

    async def remove_family_account(
        self, account_id: str, *, removed_by: str
    ) -> AccountRecord | None:
        now = datetime.now(UTC)

        async with self.accounts.database.client.start_session() as session:
            async def remove(session):
                account = await self.accounts.find_one(
                    {"account_id": account_id, "role": "family", "status": "active"},
                    session=session,
                )
                if account is None:
                    return None
                removed = await self.accounts.find_one_and_update(
                    {"account_id": account_id, "role": "family", "status": "active"},
                    {
                        "$set": {
                            "status": "disabled",
                            "updated_at": now,
                            "removed_by_account_id": removed_by,
                        },
                        "$inc": {"auth_version": 1},
                    },
                    return_document=ReturnDocument.AFTER,
                    session=session,
                )
                await self.sessions.update_many(
                    {"account_id": account_id, "revoked_at": None},
                    {"$set": {"revoked_at": now}},
                    session=session,
                )
                await self.database.account_elderly_relationships.update_many(
                    {"account_id": account_id, "status": "active"},
                    {
                        "$set": {
                            "status": "revoked",
                            "revoked_at": now,
                            "updated_at": now,
                        }
                    },
                    session=session,
                )
                await self.database.telegram_bindings.update_many(
                    {"account_id": account_id, "revoked_at": None},
                    {"$set": {"revoked_at": now}},
                    session=session,
                )
                await self.database.telegram_link_tokens.delete_many(
                    {"account_id": account_id, "consumed_at": None},
                    session=session,
                )
                return removed

            document = await session.with_transaction(remove)
        return _account_record(document) if document is not None else None

    async def bootstrap_admin(self, request: BootstrapAdminCreate) -> AccountRecord:
        if await self.accounts.count_documents({}) != 0:
            raise BootstrapUnavailable("Administrator bootstrap is no longer available")
        now = datetime.now(UTC)
        document = {
            "account_id": str(uuid4()),
            "login_name": normalize_login_name(request.login_name),
            "display_name": request.display_name.strip(),
            "role": "admin",
            "status": "active",
            "password_hash": hash_password(request.password),
            "auth_version": 1,
            "created_at": now,
            "updated_at": now,
        }
        try:
            await self.accounts.insert_one(document)
        except Exception as error:
            if getattr(error, "code", None) == 11000:
                raise BootstrapUnavailable("Administrator bootstrap is no longer available") from error
            raise
        return _account_record(document)

    async def login(self, request: LoginRequest) -> tuple[str, datetime, AccountRecord]:
        document = await self.accounts.find_one(
            {"login_name": normalize_login_name(request.login_name)}
        )
        if (
            document is None
            or document.get("status") != "active"
            or not verify_password(request.password, str(document.get("password_hash", "")))
        ):
            raise InvalidCredentials("Invalid login name or password")
        token = secrets.token_urlsafe(48)
        now = datetime.now(UTC)
        expires_at = now + timedelta(seconds=self.session_seconds)
        await self.sessions.insert_one(
            {
                "session_id": str(uuid4()),
                "token_hash": hashlib.sha256(token.encode()).hexdigest(),
                "account_id": document["account_id"],
                "auth_version": document.get("auth_version", 1),
                "created_at": now,
                "expires_at": expires_at,
                "revoked_at": None,
            }
        )
        return token, expires_at, _account_record(document)

    async def principal_from_token(self, token: str) -> Principal:
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        now = datetime.now(UTC)
        session = await self.sessions.find_one(
            {"token_hash": token_hash, "revoked_at": None, "expires_at": {"$gt": now}}
        )
        if session is None:
            raise InvalidCredentials("Authentication is required")
        account = await self.accounts.find_one({"account_id": session["account_id"]})
        if (
            account is None
            or account.get("status") != "active"
            or account.get("auth_version", 1) != session.get("auth_version", 1)
        ):
            raise InvalidCredentials("Authentication is required")
        return Principal(
            account_id=str(account["account_id"]),
            login_name=str(account["login_name"]),
            display_name=str(account["display_name"]),
            role=str(account["role"]),
            session_id=str(session["session_id"]),
        )

    async def revoke(self, principal: Principal) -> None:
        if principal.session_id:
            await self.sessions.update_one(
                {"session_id": principal.session_id, "account_id": principal.account_id},
                {"$set": {"revoked_at": datetime.now(UTC)}},
            )

    async def create_websocket_ticket(self, principal: Principal, elderly_id: str) -> str:
        token = secrets.token_urlsafe(32)
        now = datetime.now(UTC)
        await self.database.websocket_tickets.insert_one(
            {
                "token_hash": hashlib.sha256(token.encode()).hexdigest(),
                "account_id": principal.account_id,
                "elderly_id": elderly_id,
                "permission": "read_dashboard",
                "created_at": now,
                "expires_at": now + timedelta(seconds=60),
                "consumed_at": None,
            }
        )
        return token


async def get_current_principal(
    connection: HTTPConnection,
    credentials: HTTPAuthorizationCredentials | None = Depends(BEARER),
) -> Principal:
    if credentials is None or credentials.scheme.casefold() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication is required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    database = connection.app.state.database
    try:
        return await AuthService(database).principal_from_token(credentials.credentials)
    except InvalidCredentials as error:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication is required",
            headers={"WWW-Authenticate": "Bearer"},
        ) from error


async def consume_websocket_ticket(
    database: AsyncDatabase, token: str, elderly_id: str
) -> Principal:
    now = datetime.now(UTC)
    ticket = await database.websocket_tickets.find_one_and_update(
        {
            "token_hash": hashlib.sha256(token.encode()).hexdigest(),
            "elderly_id": elderly_id,
            "permission": "read_dashboard",
            "consumed_at": None,
            "expires_at": {"$gt": now},
        },
        {"$set": {"consumed_at": now}},
    )
    if ticket is None:
        raise InvalidCredentials("WebSocket authentication failed")
    account = await database.accounts.find_one(
        {"account_id": ticket["account_id"], "status": "active"}
    )
    if account is None:
        raise InvalidCredentials("WebSocket authentication failed")
    principal = Principal(
        account_id=str(account["account_id"]),
        login_name=str(account["login_name"]),
        display_name=str(account["display_name"]),
        role=str(account["role"]),
    )
    await authorize_relationship(database, principal, elderly_id, "read_dashboard")
    return principal


async def get_telemetry_principal(
    connection: HTTPConnection,
    credentials: HTTPAuthorizationCredentials | None = Depends(BEARER),
) -> Principal:
    settings = get_settings()
    if credentials is not None and credentials.scheme.casefold() == "bearer":
        if settings.telemetry_service_token and hmac.compare_digest(
            credentials.credentials, settings.telemetry_service_token
        ):
            return Principal("telemetry-service", "telemetry-service", "Telemetry service", "service")
        try:
            return await AuthService(connection.app.state.database).principal_from_token(
                credentials.credentials
            )
        except InvalidCredentials:
            pass
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Telemetry authentication is required",
        headers={"WWW-Authenticate": "Bearer"},
    )


async def authorize_relationship(
    database: AsyncDatabase,
    principal: Principal,
    elderly_id: str,
    permission: str,
) -> None:
    if principal.is_admin or principal.is_service:
        return
    relationship = await database.account_elderly_relationships.find_one(
        {
            "account_id": principal.account_id,
            "elderly_id": elderly_id,
            "status": "active",
            "permissions": permission,
        }
    )
    if relationship is None:
        raise RelationshipDenied("Resident access is not authorized")


def require_relationship_permission(permission: str):
    async def dependency(
        elderly_id: str,
        connection: HTTPConnection,
        principal: Principal = Depends(get_current_principal),
    ) -> Principal:
        if principal.is_admin or principal.is_service:
            return principal
        try:
            await authorize_relationship(
                connection.app.state.database, principal, elderly_id, permission
            )
        except RelationshipDenied as error:
            raise HTTPException(status_code=404, detail="Resident was not found") from error
        return principal

    return Depends(dependency)


async def require_admin_access(
    principal: Principal = Depends(get_current_principal),
) -> Principal:
    if not principal.is_admin:
        raise HTTPException(status_code=403, detail="Administrator access is required")
    return principal
