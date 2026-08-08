from datetime import UTC, datetime

import pytest

from app.services.telegram import TelegramDenied, TelegramService


class Accounts:
    def __init__(self, role: str = "family") -> None:
        self.role = role

    async def find_one(self, query, projection):
        if query["account_id"] != "family-1" or query["status"] != "active":
            return None
        return {"account_id": "family-1", "role": self.role}


class LinkTokens:
    def __init__(self) -> None:
        self.document = None

    async def insert_one(self, document):
        self.document = document


class Bindings:
    def __init__(self) -> None:
        self.revoked = None

    async def update_one(self, query, update):
        self.revoked = (query, update)
        return type("Result", (), {"modified_count": 1})()


class Database:
    def __init__(self, role: str = "family") -> None:
        self.accounts = Accounts(role)
        self.telegram_link_tokens = LinkTokens()
        self.telegram_bindings = Bindings()


@pytest.mark.asyncio
async def test_admin_family_link_code_targets_family_account() -> None:
    database = Database()

    response = await TelegramService(database).create_link_for_account("family-1", 600)

    assert len(response.code) > 16
    assert response.expires_at > datetime.now(UTC)
    assert database.telegram_link_tokens.document["account_id"] == "family-1"
    assert database.telegram_link_tokens.document["consumed_at"] is None


@pytest.mark.asyncio
async def test_admin_family_link_code_rejects_non_family_account() -> None:
    with pytest.raises(TelegramDenied, match="family account"):
        await TelegramService(Database(role="staff")).create_link_for_account("family-1", 600)


@pytest.mark.asyncio
async def test_admin_can_revoke_one_telegram_binding() -> None:
    database = Database()

    assert await TelegramService(database).revoke_binding("telegram-user-1") is True
    query, update = database.telegram_bindings.revoked
    assert query == {"telegram_user_id": "telegram-user-1", "revoked_at": None}
    assert "revoked_at" in update["$set"]
