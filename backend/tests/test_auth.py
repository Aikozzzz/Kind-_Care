from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.auth import AuthService, get_current_principal, hash_password, verify_password


def test_password_hash_is_salted_and_verifies() -> None:
    first = hash_password("CorrectHorseBatteryStaple!")
    second = hash_password("CorrectHorseBatteryStaple!")

    assert first != second
    assert verify_password("CorrectHorseBatteryStaple!", first)
    assert not verify_password("wrong password", first)


def test_protected_dashboard_route_requires_bearer_auth(client: TestClient) -> None:
    app.dependency_overrides.pop(get_current_principal, None)

    response = client.get("/api/elderly/E001")

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


class _Session:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def with_transaction(self, callback):
        return await callback(self)


class _Client:
    def start_session(self):
        return _Session()


class _Collection:
    def __init__(self, document=None):
        self.database = SimpleNamespace(client=_Client())
        self.document = document
        self.calls = []

    async def find_one(self, query, **kwargs):
        self.calls.append(("find_one", query, kwargs))
        if self.document is None:
            return None
        if query.get("status") == "active" and self.document.get("status") != "active":
            return None
        return self.document

    async def find_one_and_update(self, query, update, **kwargs):
        self.calls.append(("find_one_and_update", query, update, kwargs))
        self.document = {**self.document, "status": "disabled"}
        return self.document

    async def update_many(self, query, update, **kwargs):
        self.calls.append(("update_many", query, update, kwargs))

    async def delete_many(self, query, **kwargs):
        self.calls.append(("delete_many", query, kwargs))


@pytest.mark.asyncio
async def test_remove_family_account_disables_and_revokes_related_records() -> None:
    account = {
        "account_id": "family-1",
        "login_name": "family.one",
        "display_name": "Family One",
        "role": "family",
        "status": "active",
        "created_at": datetime.now(UTC),
        "updated_at": datetime.now(UTC),
    }
    database = SimpleNamespace(
        accounts=_Collection(account),
        auth_sessions=_Collection(),
        account_elderly_relationships=_Collection(),
        telegram_bindings=_Collection(),
        telegram_link_tokens=_Collection(),
    )

    removed = await AuthService(database).remove_family_account(
        "family-1", removed_by="admin-1"
    )

    assert removed is not None
    assert removed.status == "disabled"
    assert any(call[0] == "update_many" for call in database.account_elderly_relationships.calls)
    assert any(call[0] == "update_many" for call in database.telegram_bindings.calls)
    assert any(call[0] == "delete_many" for call in database.telegram_link_tokens.calls)
