from datetime import UTC, datetime

from fastapi.testclient import TestClient

from app.dependencies import get_database
from app.main import app


class Cursor:
    def __init__(self, documents: list[dict[str, object]]) -> None:
        self.documents = documents

    def sort(self, *args):
        return self

    async def to_list(self, length=None):
        return self.documents[:length] if length is not None else self.documents


class Collection:
    def __init__(self, documents: list[dict[str, object]] | None = None) -> None:
        self.documents = documents or []

    def find(self, query, projection=None):
        matches = [
            document
            for document in self.documents
            if all(
                value in document.get(key, [])
                if isinstance(value, str) and isinstance(document.get(key), list)
                else document.get(key) == value
                for key, value in query.items()
            )
        ]
        if projection:
            matches = [
                {key: document[key] for key in projection if key in document}
                for document in matches
            ]
        return Cursor(matches)

    async def find_one(self, query, projection=None):
        documents = await self.find(query, projection).to_list(length=1)
        return documents[0] if documents else None

    async def insert_one(self, document):
        self.documents.append(document)


class Database:
    def __init__(self) -> None:
        now = datetime.now(UTC)
        self.elderly_profiles = Collection([{"elderly_id": "E001", "active": True}])
        self.accounts = Collection(
            [
                {
                    "account_id": "family-1",
                    "login_name": "family.one",
                    "display_name": "Family One",
                    "role": "family",
                    "status": "active",
                }
            ]
        )
        self.account_elderly_relationships = Collection(
            [
                {
                    "relationship_id": "relationship-1",
                    "account_id": "family-1",
                    "elderly_id": "E001",
                    "relationship_type": "family",
                    "permissions": ["query_telegram_status", "receive_telegram_alerts"],
                    "status": "active",
                    "created_by_account_id": "test-admin",
                    "created_at": now,
                    "updated_at": now,
                    "revoked_at": None,
                }
            ]
        )
        self.telegram_bindings = Collection(
            [
                {
                    "account_id": "family-1",
                    "telegram_user_id": "telegram-1",
                    "chat_type": "private",
                    "linked_at": now,
                    "revoked_at": None,
                }
            ]
        )
        self.telegram_link_tokens = Collection()


def test_admin_can_list_family_relationships_and_telegram_bindings(
    client: TestClient,
) -> None:
    database = Database()
    app.dependency_overrides[get_database] = lambda: database

    relationships = client.get("/api/relationships?elderly_id=E001")
    bindings = client.get("/api/telegram/admin/bindings?elderly_id=E001")

    assert relationships.status_code == 200
    assert relationships.json()["data"][0]["account_display_name"] == "Family One"
    assert bindings.status_code == 200
    assert bindings.json()["data"][0]["receive_telegram_alerts"] is True


def test_admin_can_issue_family_targeted_telegram_link_code(client: TestClient) -> None:
    database = Database()
    app.dependency_overrides[get_database] = lambda: database

    response = client.post(
        "/api/telegram/admin/link/family-1",
        json={"expires_in_seconds": 600},
    )

    assert response.status_code == 200
    assert response.json()["data"]["code"]
    assert database.telegram_link_tokens.documents[0]["account_id"] == "family-1"
