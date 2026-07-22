from datetime import UTC, datetime
from uuid import UUID

import pytest

from app.models.health import HealthEventCreate
from app.services.health import HealthEventService


NOW = datetime(2026, 7, 20, 12, 0, tzinfo=UTC)


class Session:
    async def with_transaction(self, callback):
        return await callback(self)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None


class Client:
    def __init__(self):
        self.session = Session()

    def start_session(self):
        return self.session


class Database:
    def __init__(self):
        self.client = Client()


class Profiles:
    def __init__(self, session):
        self.session = session

    async def find_one(self, query, projection, *, session=None):
        assert session is self.session
        return {"_id": "profile"}


class Reservations:
    def __init__(self, database, session):
        self.database = database
        self.session = session

    async def find_one_and_update(self, query, update, **options):
        assert options["session"] is self.session
        return update["$setOnInsert"]


class Dispatcher:
    def dispatch(self, event):
        self.event = event


@pytest.mark.asyncio
async def test_profile_and_reservation_are_checked_in_one_transaction() -> None:
    database = Database()
    service = HealthEventService(
        Profiles(database.client.session),
        Reservations(database, database.client.session),
        object(),
        object(),
        Dispatcher(),
    )
    request = HealthEventCreate(
        elderly_id="E001",
        heart_rate=80,
        temperature=36.7,
        oxygen_level=97,
        movement_status="active",
        medicine_status="taken",
        recorded_at=NOW,
    )

    event = await service.queue_event(
        request,
        UUID("00000000-0000-5000-8000-000000000001"),
        "transaction-key",
    )

    assert event.recorded_at == NOW
