from datetime import UTC, datetime

import pytest

from app.routes.telegram_internal import claim_notification


class Events:
    def __init__(self) -> None:
        self.updated = []

    async def find_one_and_update(self, *args, **kwargs):
        return {
            "notification_event_id": "alert-1:created",
            "alert_id": "alert-1",
            "elderly_id": "E001",
            "created_at": datetime.now(UTC),
        }

    async def update_one(self, query, update):
        self.updated.append((query, update))


class Alerts:
    async def find_one(self, query):
        return {"alert_id": "alert-1", "elderly_id": "E001"}


class Profiles:
    async def find_one(self, query, projection):
        return None


class Database:
    def __init__(self) -> None:
        self.alert_notification_events = Events()
        self.alerts = Alerts()
        self.elderly_profiles = Profiles()


@pytest.mark.asyncio
async def test_archived_profile_notification_is_closed_without_delivery() -> None:
    database = Database()

    assert await claim_notification(database, None) is None
    assert len(database.alert_notification_events.updated) == 1
    query, update = database.alert_notification_events.updated[0]
    assert query == {"notification_event_id": "alert-1:created"}
    assert update["$set"]["status"] == "sent"
